import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, Response
from openai import OpenAI
import httpx
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
except Exception:  # optional dependency
    Limiter = None
from flask_cors import CORS
import json
import random
from pathlib import Path
import re
import uuid
import time

# Load environment variables
load_dotenv()

# ---------- Centralized Config ----------
class Config:
    ENV = os.getenv('ENV', 'development')
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
    PORT = int(os.getenv('PORT', '5050'))
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    ALLOWED_ORIGINS = [o.strip() for o in (os.getenv('ALLOWED_ORIGINS') or '').split(',') if o.strip()]
    MAX_TURN_CHARS = int(os.getenv('MAX_TURN_CHARS', '600'))
    MAX_TTS_CHARS = int(os.getenv('MAX_TTS_CHARS', '500'))
    DEFAULT_RATE_LIMIT = os.getenv('DEFAULT_RATE_LIMIT', '100/hour')
    RATE_LIMIT_TTS = os.getenv('RATE_LIMIT_TTS', '20/minute')
    RATE_LIMIT_ROLEPLAY_TURN = os.getenv('RATE_LIMIT_ROLEPLAY_TURN', '40/minute')
    RATE_LIMIT_ROLEPLAY_FEEDBACK = os.getenv('RATE_LIMIT_ROLEPLAY_FEEDBACK', '20/minute')
    RATE_LIMIT_MATCHING = os.getenv('RATE_LIMIT_MATCHING', '60/minute')
    
    # Data collection settings
    ENABLE_ANALYTICS = os.getenv('ENABLE_ANALYTICS', 'true').lower() == 'true'
    ANALYTICS_SAMPLE_RATE = float(os.getenv('ANALYTICS_SAMPLE_RATE', '1.0'))  # 1.0 = 100%

app = Flask(__name__, static_folder='static', template_folder='templates')

# Logging
logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
                    format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# CORS policy: default deny (no CORS). If ALLOWED_ORIGINS is set, enable narrowly.
if Config.ALLOWED_ORIGINS:
    CORS(app, resources={r"/*": {"origins": Config.ALLOWED_ORIGINS,
                                  "methods": ["GET", "POST", "OPTIONS"],
                                  "allow_headers": ["Content-Type", "Authorization"]}})
else:
    # No CORS headers added (same-origin only)
    pass

# Configure OpenAI
OPENAI_API_KEY = Config.OPENAI_API_KEY
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Optional rate limiter
limiter = None
if Limiter is not None:
    try:
        limiter = Limiter(get_remote_address, app=app, default_limits=[Config.DEFAULT_RATE_LIMIT])
    except Exception as _e:
        logger.warning("Rate limiter not initialized: %s", _e)

# System message to set the behavior of the assistant
SYSTEM_MESSAGE = {
    "role": "system",
    "content": (
        "You are Emily (李爱), a patient Mandarin teacher for beginning learners. "
        "Default style (for general chat): reply in Chinese with pinyin in parentheses and then concise English. "
        "Keep responses short and encouraging."
    ),
}

# ---------- Data Collection & Analytics ----------
def log_user_interaction(event_type, data, user_id=None, session_id=None):
    """Log user interactions for pilot study analysis"""
    if not Config.ENABLE_ANALYTICS:
        return
    
    # Sample rate check
    if random.random() > Config.ANALYTICS_SAMPLE_RATE:
        return
    
    try:
        # Create analytics directory
        analytics_dir = Path(__file__).parent / 'analytics'
        analytics_dir.mkdir(exist_ok=True)
        
        # Create log entry
        log_entry = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'event_type': event_type,
            'user_id': user_id or 'anonymous',
            'session_id': session_id or str(uuid.uuid4())[:8],
            'data': data,
            'ip_hash': hash(request.remote_addr) if request and request.remote_addr else None
        }
        
        # Write to daily log file
        log_file = analytics_dir / f"pilot_data_{datetime.utcnow().strftime('%Y%m%d')}.jsonl"
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            
    except Exception as e:
        logger.warning(f"Analytics logging failed: {e}")

def get_session_id():
    """Get or create session ID from request headers"""
    return request.headers.get('X-Session-ID', str(uuid.uuid4())[:8])

# Load units configuration
UNITS_PATH = Path(__file__).parent / "config" / "units.json"
with open(UNITS_PATH, "r", encoding="utf-8") as f:
    UNITS_CONFIG = json.load(f)

def get_unit(unit_id: str):
    for u in UNITS_CONFIG.get("units", []):
        if u.get("id") == unit_id:
            return u
    return None

def build_vocab_index(unit):
    """Build quick lookups for hanzi -> {pinyin, english}."""
    vocab = unit.get('vocab', []) if unit else []
    by_hanzi = {v['hanzi']: { 'pinyin': v.get('pinyin', ''), 'english': v.get('english', '') } for v in vocab}
    return by_hanzi

def _parse_eng_base_pos(meaning: str):
    """Parse an English gloss like 'teacher (noun)' into (base, pos).
    Returns (base, pos_lower) where pos_lower is e.g. 'noun', 'verb', 'adjective', 'pronoun', 'phrase', 'particle', 'number', 'question word', 'measure word'."""
    if not meaning:
        return '', ''
    base = meaning
    pos = ''
    if ' (' in meaning and meaning.endswith(')'):
        base = meaning.split(' (', 1)[0]
        pos = meaning.rsplit('(', 1)[-1].rstrip(')').strip().lower()
    return base, pos

def _object_pronoun_from_base(eng_base: str) -> str:
    """Given an English base like 'they, them' or 'he, him', return an object-form
    pronoun to fit contexts like 'I like ___'. If no mapping, return the input.
    """
    if not eng_base:
        return eng_base
    base_lower = eng_base.lower().strip()
    mappings = {
        'they, them': 'them',
        'he, him': 'him',
        'she, her': 'her',
        'we, us': 'us',
        'i, me': 'me',
        'you (plural pronoun)': 'you',
        'you (polite, pronoun)': 'you',
        'you (pronoun)': 'you',
    }
    # Direct map
    if base_lower in mappings:
        return mappings[base_lower]
    # If comma-separated forms, prefer last as an approximation of object case
    if ',' in base_lower:
        return base_lower.split(',')[-1].strip()
    return eng_base

def generate_sample_sentence(unit_id: str, hanzi: str, pinyin: str, meaning: str):
    """Generate a simple, natural sample sentence containing the target hanzi.
    Returns dict with chinese, pinyin, english.
    We keep patterns short and beginner-friendly.
    """
    eng_base, pos = _parse_eng_base_pos(meaning)
    pos = pos.lower()
    def has(tag: str) -> bool:
        return tag in pos if pos else False
    greetings = { '你好', '您好', '你们好', '再见' }
    # Heuristic lists for safer templates
    person_keywords = {
        'person','people','man','woman','boy','girl','teacher','student','friend','grandpa','grandma','dad','mom','mother','father','aunt','uncle','classmate','child','son','daughter'
    }
    # Treat these hanzi as people-role nouns regardless of English gloss
    person_hanzi = {
        '人','男人','女人','男孩子','女孩子','老师','学生','朋友','爷爷','奶奶','爸爸','妈妈','儿子','女儿','同学','叔叔','阿姨','哥哥','姐姐','弟弟','妹妹','中国人','美国人'
    }
    # Unit-specific quick templates (prefer these when available)
    unit2_map = {
        '爸爸': ("这是我爸爸。", "Zhè shì wǒ {p}.", "This is my dad."),
        '妈妈': ("这是我妈妈。", "Zhè shì wǒ {p}.", "This is my mom."),
        '爷爷': ("我爱我的爷爷。", "Wǒ ài wǒ de {p}.", "I love my grandpa."),
        '奶奶': ("奶奶很亲切。", "{p} hěn qīnqiè.", "Grandma is kind."),
        '姥爷': ("我去看姥爷。", "Wǒ qù kàn {p}.", "I go to visit my grandpa (mom's side)."),
        '姥姥': ("姥姥在家。", "{p} zài jiā.", "Grandma (mom's side) is at home."),
        '叔叔': ("叔叔来了。", "{p} lái le.", "Uncle is here."),
        '阿姨': ("阿姨很好。", "{p} hěn hǎo.", "Aunt is nice."),
        '哥哥': ("我有一个哥哥。", "Wǒ yǒu yí gè {p}.", "I have an older brother."),
        '姐姐': ("我有一个姐姐。", "Wǒ yǒu yí gè {p}.", "I have an older sister."),
        '弟弟': ("我有一个弟弟。", "Wǒ yǒu yí gè {p}.", "I have a younger brother."),
        '妹妹': ("我有一个妹妹。", "Wǒ yǒu yí gè {p}.", "I have a younger sister."),
        '兄弟姐妹': ("我有兄弟姐妹。", "Wǒ yǒu {p}.", "I have siblings."),
        '儿子': ("他有一个儿子。", "Tā yǒu yí gè {p}.", "He has a son."),
        '女儿': ("她有一个女儿。", "Tā yǒu yí gè {p}.", "She has a daughter."),
        '家':   ("我爱我的家。", "Wǒ ài wǒ de {p}.", "I love my family/home."),
        '人':   ("这里有很多人。", "Zhèlǐ yǒu hěn duō {p}.", "There are many people here."),
        '男':   ("他是男生。", "Tā shì {p} shēng.", "He is a boy."),
        '女':   ("她是女生。", "Tā shì {p} shēng.", "She is a girl."),
        '男孩子': ("那个男孩子是我朋友。", "Nà gè {p} shì wǒ péngyou.", "That boy is my friend."),
        '女孩子': ("这个女孩子很可爱。", "Zhè gè {p} hěn kě'ài.", "This girl is cute."),
        '有':   ("我有两个姐妹。", "Wǒ {p} liǎng gè jiěmèi.", "I have two sisters."),
        '没(有)': ("我没有兄弟。", "Wǒ méi(yǒu) xiōngdì.", "I don't have brothers."),
        '和':   ("我和他是同学。", "Wǒ {p} tā shì tóngxué.", "He and I are classmates."),
        '几':   ("你家有几口人？", "Nǐ jiā yǒu {p} kǒu rén?", "How many people are in your family?"),
        '两':   ("我们家有两个人。", "Wǒmen jiā yǒu {p} gè rén.", "There are two people in my family."),
        '宠物': ("我有一个宠物。", "Wǒ yǒu yí gè {p}.", "I have a pet."),
        '猫':   ("我有一只猫。", "Wǒ yǒu yì zhī {p}.", "I have a cat."),
        '狗':   ("我喜欢狗。", "Wǒ xǐhuān {p}.", "I like dogs."),
        '鸟':   ("那只鸟很小。", "Nà zhī {p} hěn xiǎo.", "That bird is small."),
        '只':   ("我有两只狗。", "Wǒ yǒu liǎng zhī gǒu.", "I have two dogs."),
        '中国': ("我的朋友来自中国。", "Wǒ de péngyou láizì {p}.", "My friend is from China."),
        '中国人': ("她是中国人。", "Tā shì {p}.", "She is Chinese."),
        '美国': ("我在美国上学。", "Wǒ zài {p} shàngxué.", "I study in the USA."),
        '美国人': ("我是美国人。", "Wǒ shì {p}.", "I am American."),
        '英国': ("他去英国学习。", "Tā qù {p} xuéxí.", "He is going to study in the UK."),
        '法国': ("我朋友住在法国。", "Wǒ péngyou zhù zài {p}.", "My friend lives in France."),
        '哪国': ("你是哪国人？", "Nǐ shì {p} rén?", "Which country are you from?"),
        '哪里': ("你家在哪里？", "Nǐ jiā zài {p}?", "Where is your home?"),
        '哪儿': ("你去哪儿？", "Nǐ qù {p}?", "Where are you going?"),
    }
    unit3_map = {
        '今天': ("今天我有中文课。", "{p} wǒ yǒu Zhōngwén kè.", "I have Chinese class today."),
        '明天': ("明天我们有活动。", "{p} wǒmen yǒu huódòng.", "We have an activity tomorrow."),
        '昨天': ("昨天我学习了中文。", "{p} wǒ xuéxí le Zhōngwén.", "Yesterday I studied Chinese."),
        '现在': ("现在是三点。", "Xiànzài shì sān diǎn.", "It is three o'clock now."),
        '点': ("现在一点。", "Xiànzài yì diǎn.", "It is one o'clock."),
        '分': ("现在十分。", "Xiànzài shí fēn.", "It is ten minutes past."),
        '半': ("现在两点半。", "Xiànzài liǎng diǎn bàn.", "It is half past two."),
        '刻': ("现在三点一刻。", "Xiànzài sān diǎn yí kè.", "It is a quarter past three."),
        '早上': ("我早上上学。", "Wǒ {p} shàngxué.", "I go to school in the morning."),
        '上午': ("我上午上课。", "Wǒ shàngwǔ {p}.", "I have class in the morning."),
        '中午': ("我中午吃饭。", "Wǒ zhōngwǔ chīfàn.", "I eat lunch at noon."),
        '下午': ("我下午下课。", "Wǒ {p} xiàkè.", "I finish class in the afternoon."),
        '晚上': ("我晚上做作业。", "Wǒ {p} zuò zuòyè.", "I do homework in the evening."),
        '上课': ("我早上上课。", "Wǒ zǎoshang {p}.", "I have class in the morning."),
        '下课': ("我们四点下课。", "Wǒmen sì diǎn {p}.", "We finish class at four."),
        '见':   ("我明天见老师。", "Wǒ míngtiān {p} lǎoshī.", "I will meet the teacher tomorrow."),
        '活动': ("今天学校有活动。", "Jīntiān xuéxiào yǒu {p}.", "The school has an activity today."),
        '中文课': ("我喜欢中文课。", "Wǒ xǐhuān {p}.", "I like Chinese class."),
        '数学': ("我明天有数学课。", "Wǒ míngtiān yǒu {p} kè.", "I have math class tomorrow."),
        '历史': ("他在学历史。", "Tā zài xué {p}.", "He is studying history."),
        '科学': ("我们喜欢科学。", "Wǒmen xǐhuān {p}.", "We like science."),
        '艺术': ("她学艺术。", "Tā xué {p}.", "She studies art."),
        '计算机': ("我在学计算机。", "Wǒ zài xué {p}.", "I am studying computer science."),
        '政治': ("他对政治感兴趣。", "Tā duì {p} gǎn xìngqù.", "He is interested in politics."),
        '起床': ("我六点起床。", "Wǒ liù diǎn {p}.", "I get up at six."),
        '睡觉': ("我十点睡觉。", "Wǒ shí diǎn {p}.", "I go to sleep at ten."),
        '上学': ("我七点上学。", "Wǒ qī diǎn {p}.", "I go to school at seven."),
        '放学': ("我们三点放学。", "Wǒmen sān diǎn {p}.", "We finish school at three."),
        '学习': ("我每天学习。", "Wǒ měitiān {p}.", "I study every day."),
        '运动': ("我喜欢运动。", "Wǒ xǐhuān {p}.", "I like sports."),
        '上网': ("我晚上上网。", "Wǒ wǎnshang {p}.", "I go online in the evening."),
        '吃':   ("我吃早饭。", "Wǒ {p} zǎofàn.", "I eat breakfast."),
        '吃饭': ("我们一起吃饭。", "Wǒmen yìqǐ {p}.", "We eat together."),
        '早饭': ("我七点吃早饭。", "Wǒ qī diǎn chī {p}.", "I eat breakfast at seven."),
        '午饭': ("我中午吃午饭。", "Wǒ zhōngwǔ chī {p}.", "I eat lunch at noon."),
        '晚饭': ("我六点吃晚饭。", "Wǒ liù diǎn chī {p}.", "I eat dinner at six."),
        '什么时候': ("你什么时候上课？", "Nǐ {p} shàngkè?", "When do you have class?"),
        '以前': ("以前我在北京。", "Yǐqián wǒ zài Běijīng.", "Before, I was in Beijing."),
        '以后': ("以后我想学中文。", "Yǐhòu wǒ xiǎng xué Zhōngwén.", "Later, I want to study Chinese."),
        '从...到...': ("我从八点到九点上课。", "Wǒ cóng bā diǎn dào jiǔ diǎn shàngkè.", "I have class from 8 to 9."),
        '学校': ("我在学校学习。", "Wǒ zài {p} xuéxí.", "I study at school."),
        '图书馆': ("我在图书馆看书。", "Wǒ zài {p} kàn shū.", "I read in the library."),
        '教室': ("我们在教室上课。", "Wǒmen zài {p} shàngkè.", "We have class in the classroom."),
        '办公室': ("老师在办公室。", "Lǎoshī zài {p}.", "The teacher is in the office."),
        '宿舍': ("我回宿舍。", "Wǒ huí {p}.", "I return to the dorm."),
        '体育馆': ("我们去体育馆。", "Wǒmen qù {p}.", "We go to the gym."),
        '操场': ("他们在操场运动。", "Tāmen zài {p} yùndòng.", "They exercise on the field."),
        '食堂': ("我们在食堂吃饭。", "Wǒmen zài {p} chīfàn.", "We eat in the cafeteria."),
        '中心': ("学校中心在那边。", "Xuéxiào {p} zài nà biān.", "The school center is over there."),
        '楼': ("这个楼很高。", "Zhège {p} hěn gāo.", "This building is tall."),
        '一楼': ("教室在一楼。", "Jiàoshì zài yī lóu.", "The classroom is on the first floor."),
    }

    # Direct special-cases for single characters that need context
    if hanzi == '呢':
        return {
            'chinese': '我是学生。你呢？',
            'pinyin': 'Wǒ shì xuéshēng. Nǐ ne?',
            'english': 'I am a student. How about you?'
        }

    if unit_id == 'unit2' and hanzi in unit2_map:
        ch, py_t, en = unit2_map[hanzi]
        return { 'chinese': ch, 'pinyin': py_t.format(p=pinyin), 'english': en }
    if unit_id == 'unit3' and hanzi in unit3_map:
        ch, py_t, en = unit3_map[hanzi]
        return { 'chinese': ch, 'pinyin': py_t.format(p=pinyin), 'english': en }
    # Common course/subject nouns
    if hanzi in ('中文','汉语'):
        return { 'chinese': f"我喜欢学{hanzi}。", 'pinyin': f"Wǒ xǐhuān xué {pinyin}.", 'english': "I like studying Chinese." }
    # Defaults if we cannot classify
    chinese = f"我喜欢{hanzi}。"
    pinyin_line = f"Wǒ xǐhuān {pinyin}."
    natural_obj = _object_pronoun_from_base(eng_base or meaning or '')
    english = f"I like {natural_obj or (eng_base or meaning)}."

    # Greetings/expressions
    if hanzi in greetings or has('expression') or pos == 'phrase':
        chinese = f"{hanzi}!"
        pinyin_line = f"{pinyin}!"
        english = eng_base or meaning or "(expression)"
    # Pronouns
    elif has('pronoun'):
        if hanzi in ('你','您'):
            chinese = "你是我的朋友。"
            pinyin_line = "Nǐ shì wǒ de péngyou."
            english = "You are my friend."
        elif hanzi in ('他','她','他们','她们'):
            chinese = f"{hanzi}是学生。"
            # Map common pronouns to pinyin first syllable where needed
            pron_map = {'他':'Tā','她':'Tā','他们':'Tāmen','她们':'Tāmen'}
            pinyin_line = f"{pron_map.get(hanzi,'Tā')} shì xuéshēng."
            english = "He/She/They are students."
        elif hanzi in ('我们','你们'):
            chinese = f"{hanzi}是同学。"
            grp_map = {'我们':'Wǒmen','你们':'Nǐmen'}
            pinyin_line = f"{grp_map.get(hanzi,'Wǒmen')} shì tóngxué."
            english = "We/You are classmates."
        elif hanzi == '我':
            chinese = "我是学生。"
            pinyin_line = "Wǒ shì xuéshēng."
            english = "I am a student."
    # Verbs
    elif has('verb') or eng_base.startswith('to '):
        # Keep the extremely common copula and action patterns
        if hanzi == '是':
            chinese = "他是老师。"
            pinyin_line = "Tā shì lǎoshī."
            english = "He is a teacher."
        elif hanzi == '姓':
            # Avoid unnatural 'I surname Chinese.' Use a common question pattern instead
            chinese = "你姓什么？"
            pinyin_line = "Nǐ xìng shénme?"
            english = "What is your surname?"
        elif hanzi == '叫':
            # Common natural question using 叫
            chinese = "你叫什么名字？"
            pinyin_line = "Nǐ jiào shénme míngzi?"
            english = "What is your name?"
        else:
            chinese = f"我{hanzi}中文。"
            pinyin_line = f"Wǒ {pinyin} Zhōngwén."
            english = f"I {eng_base or '...'} Chinese."
    # Adjectives
    elif has('adjective'):
        # Basic '很' pattern for adjectives
        chinese = f"他很{hanzi}。"
        pinyin_line = f"Tā hěn {pinyin}."
        english = f"He is very {eng_base or meaning}."
        if hanzi == '好':
            chinese = "我很好。"
            pinyin_line = "Wǒ hěn hǎo."
            english = "I am fine."
    # Nouns
    elif has('noun') or has('proper'):
        # Special safe case for '名字' (name)
        if hanzi == '名字' or 'name' in (eng_base or meaning).lower():
            options = [
                ("我的名字是李爱。", "Wǒ de míngzi shì Lǐ Ài.", "My name is Li Ai."),
                ("我的中文名字是高天。", "Wǒ de Zhōngwén míngzi shì Gāo Tiān.", "My Chinese name is Gao Tian."),
                ("我没有英文名字。", "Wǒ méiyǒu Yīngwén míngzi.", "I don't have an English name."),
            ]
            ch, py, en = random.choice(options)
            chinese = ch; pinyin_line = py; english = en
        # Language handled above; also avoid treating subjects or abstract nouns as people
        elif (hanzi in person_hanzi) or any(k in (eng_base or meaning).lower() for k in person_keywords):
            chinese = f"他是{hanzi}。"
            pinyin_line = f"Tā shì {pinyin}."
            english = f"He is a {eng_base or meaning}."
        else:
            # Neutral demonstrative for objects/abstract nouns
            chinese = f"这是{hanzi}。"
            pinyin_line = f"Zhè shì {pinyin}."
            english = f"This is {eng_base or meaning}."
    # Numbers
    elif has('number'):
        chinese = f"我最喜欢的数字是{hanzi}。"
        pinyin_line = f"Wǒ zuì xǐhuān de shùzì shì {pinyin}."
        english = f"My favorite number is {eng_base or meaning}."
    # Question words
    elif has('question'):
        if hanzi in ('谁','哪儿','哪里'):
            q_map = {
                '谁': ("他是谁？", "Tā shì shéi?", "Who is he?"),
                '哪儿': ("你去哪儿？", "Nǐ qù nǎr?", "Where are you going?"),
                '哪里': ("你去哪里？", "Nǐ qù nǎlǐ?", "Where are you going?"),
            }
            chinese, pinyin_line, english = q_map[hanzi]
        else:
            chinese = f"这是{hanzi}吗？"
            pinyin_line = f"Zhè shì {pinyin} ma?"
            english = f"Is this {eng_base or meaning}?"
    # Particles / measure words: demonstrate in a short, common pattern
    elif has('particle'):
        if hanzi == '吗':
            chinese = "你好吗？"
            pinyin_line = "Nǐ hǎo ma?"
            english = "How are you?"
        elif hanzi == '的':
            chinese = "这是我的老师。"
            pinyin_line = "Zhè shì wǒ de lǎoshī."
            english = "This is my teacher."
    elif has('measure'):
        chinese = "我有一个朋友。"
        pinyin_line = "Wǒ yǒu yí gè péngyou."
        english = "I have one friend."

    return { 'chinese': chinese, 'pinyin': pinyin_line, 'english': english }

def generate_ai_matching_feedback(unit, accuracy: int, incorrect_items: list, mode: str):
    """Ask OpenAI to produce an encouraging overall feedback and short memorization tips per mistaken hanzi.
    Returns dict: { 'overall': str, 'tipsByHanzi': { hanzi: [str, ...] } }
    """
    if client is None:
        return None
    try:
        unit_title = unit.get('title') if unit else ''
        try:
            unit_vocab = [
                { 'hanzi': v.get('hanzi',''), 'pinyin': v.get('pinyin',''), 'english': v.get('english','') }
                for v in (unit.get('vocab', []) if unit else [])
            ]
        except Exception:
            unit_vocab = []

        # Prepare a compact payload with only necessary fields
        mistakes = []
        for it in incorrect_items or []:
            mistakes.append({
                'hanzi': it.get('leftHanzi',''),
                'pinyin': it.get('pinyin',''),
                'meaning': it.get('meaning',''),
                'expected': it.get('expectedEnglish') if mode=='english' else it.get('expectedPinyin'),
                'chosen': it.get('rightValue',''),
                'radicalsByChar': it.get('radicalsByChar',[]),
                'sample': it.get('sample',{}),
            })

        sys = { 'role':'system', 'content': (
            "You are Emily (李爱), a patient Mandarin teacher for beginners."
            " Generate very short, encouraging feedback and 1-2 concrete memorization tips per mistaken item."
            " Tips must focus on what each character means and how key radicals/components hint at meaning or pronunciation."
            " For multi-character words, briefly mention each character’s meaning."
            " Keep language simple. If including Chinese, add pinyin in parentheses, then a short English gloss."
        )}
        user_prompt = {
            'role':'user',
            'content': (
                "Create JSON with fields: overall (string) and tipsByHanzi (object mapping hanzi->array of short tips). "
                "Context: Unit: " + str(unit_title) + "; Stage mode: " + str(mode) + "; Accuracy: " + str(accuracy) + ". "
                "Use ONLY the current unit vocabulary provided to compose any Chinese sample phrases (and ALWAYS include the mistaken hanzi in its example). "
                "Focus tips on explaining the character’s meaning and connecting radicals/components to meaning or pronunciation; for multi-character words, cover each character briefly. "
                "Keep tips concrete and short (max 1-2 per hanzi). Provide Chinese with pinyin in parentheses, then a short English gloss. "
                "UnitVocabulary: " + json.dumps(unit_vocab, ensure_ascii=False) + "\n" +
                "Mistakes: " + json.dumps(mistakes, ensure_ascii=False)
            )
        }
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[sys, user_prompt],
            temperature=0.4,
            max_tokens=400
        )
        txt = resp.choices[0].message.content or ''
        start = txt.find('{')
        end = txt.rfind('}')
        if start != -1 and end != -1 and end > start:
            payload = json.loads(txt[start:end+1])
            overall = payload.get('overall')
            tips = payload.get('tipsByHanzi') or {}
            if isinstance(tips, dict):
                return { 'overall': overall, 'tipsByHanzi': tips }
        return None
    except Exception:
        return None

def generate_ai_sample_sentence(unit, hanzi: str, pinyin: str, english: str):
    """Ask OpenAI to create a short, natural sample sentence that MUST include the target hanzi.
    Constrain any extra vocabulary to the current unit's word list. Return dict {chinese,pinyin,english} or None.
    """
    if client is None:
        return None
    try:
        unit_title = unit.get('title') if unit else ''
        unit_vocab = []
        try:
            unit_vocab = [
                {
                    'hanzi': v.get('hanzi',''),
                    'pinyin': v.get('pinyin',''),
                    'english': v.get('english',''),
                }
                for v in (unit.get('vocab', []) if unit else [])
            ]
        except Exception:
            unit_vocab = []
        sys = {
            'role':'system',
            'content': (
                "You are a careful Mandarin content writer for beginners."
                " Output ONLY JSON: {chinese:string, pinyin:string, english:string}."
                " Create ONE short, natural sentence that MUST include the target hanzi exactly as given."
                " Use ONLY words from the provided unit vocabulary besides the target hanzi."
                " Keep grammar simple and beginner-friendly."
            )
        }
        user = {
            'role':'user',
            'content': (
                "Unit: " + str(unit_title) + ". TargetHanzi: " + str(hanzi) + "; TargetPinyin: " + str(pinyin) + "; TargetEnglish: " + str(english) + ".\n"
                "UnitVocabulary: " + json.dumps(unit_vocab, ensure_ascii=False)
            )
        }
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[sys, user],
            temperature=0.3,
            max_tokens=160
        )
        txt = resp.choices[0].message.content or ''
        s = txt.find('{'); e = txt.rfind('}')
        if s != -1 and e != -1 and e > s:
            try:
                payload = json.loads(txt[s:e+1])
                cn = (payload.get('chinese') or '').strip()
                py = (payload.get('pinyin') or '').strip()
                en = (payload.get('english') or '').strip()
                if cn and hanzi in cn:
                    return { 'chinese': cn, 'pinyin': py, 'english': en }
            except Exception:
                return None
        return None
    except Exception:
        return None

@app.route('/')
def index():
    # Log page visit
    log_user_interaction('page_visit', {
        'page': 'home',
        'user_agent': request.headers.get('User-Agent', '')[:200]
    }, session_id=get_session_id())
    return render_template('index.html')

# ---- Unit 2 Roleplay helpers: strict question list and simple progress tracking ----
def normalize_apologies(text: str) -> str:
    """Normalize apology wording to use 对不起 instead of 抱歉 (and variants)."""
    try:
        if not text:
            return text
        # Replace standalone and modified forms (e.g., 很抱歉/真抱歉) with 对不起
        return re.sub(r"抱歉", "对不起", text)
    except Exception:
        return text
def _unit2_questions():
    """Canonical Unit 2 target questions (Chinese). Keep order strict."""
    return [
        "你是哪国人？你是哪里人？",
        "你家有几口人？都有谁？",
        "你有几个哥哥？",
        "你有几个弟弟？",
        "你有几个姐姐？",
        "你有几个妹妹？",
        "你有宠物吗？是什么？",
        "你爸爸妈妈多大？",
        "你多大？",
        "她的哥哥也是老师吗？",
        "她的妹妹在哪儿？",
        "她的妹妹几年级？",
    ]

def _unit2_progress_hint(history):
    """Create a lightweight hint about which Unit 2 questions seem covered.
    We use simple keyword heuristics to help the model; the model remains the source of truth.
    Returns dict with keys: asked_indices (list[int]), next_index (int), remaining (list[str]).
    """
    qs = _unit2_questions()
    asked = set()
    # naive keyword map per index
    kw = {
        0: ["哪国", "哪里人"],
        1: ["几口人", "都有谁", "口"],
        2: ["几个哥哥", "哥哥"],
        3: ["几个弟弟", "弟弟"],
        4: ["几个姐姐", "姐姐"],
        5: ["几个妹妹", "妹妹"],
        6: ["宠物", "猫", "狗", "鸟"],
        7: ["爸爸", "妈妈", "多大"],
        8: ["你多大", "岁"],
        9: ["哥哥", "老师"],
        10: ["妹妹", "在哪儿", "哪里"],
        11: ["妹妹", "几年级"],
    }
    lines = []
    for m in history or []:
        c = (m.get('content') or '').strip()
        if c:
            lines.append(c)
    joined = "\n".join(lines)
    for i, kws in kw.items():
        if any(k in joined for k in kws):
            asked.add(i)
    asked_indices = sorted(list(asked))
    next_index = 0
    for i in range(len(qs)):
        if i not in asked:
            next_index = i
            break
    remaining = [qs[i] for i in range(len(qs)) if i not in asked]
    return { 'asked_indices': asked_indices, 'next_index': next_index, 'remaining': remaining }

def _infer_student_facts(history):
    """Infer simple boolean facts from student (user) turns to guide follow-ups.
    Detects presence/absence of siblings to avoid inappropriate questions.
    Returns dict like { has_older_brother: True/False/None, has_younger_brother: True/False/None, ... }.
    """
    facts = {
        'has_older_brother': None,
        'has_younger_brother': None,
        'has_older_sister': None,
        'has_younger_sister': None,
        'has_pet': None,
    }
    # Only consider user turns
    user_lines = []
    for m in history or []:
        if m.get('role') == 'user':
            c = (m.get('content') or '').strip()
            if c:
                user_lines.append(c)
    text = "\n".join(user_lines)
    try:
        # Normalize ASCII/whitespace
        t = text.replace('\u3000', ' ').strip()
        # Heuristics for negation
        neg_patterns = [
            (r"我\s*没有\s*哥哥", 'has_older_brother', False),
            (r"没有\s*哥哥", 'has_older_brother', False),
            (r"我\s*没有\s*弟弟", 'has_younger_brother', False),
            (r"没有\s*弟弟", 'has_younger_brother', False),
            (r"我\s*没有\s*姐姐", 'has_older_sister', False),
            (r"没有\s*姐姐", 'has_older_sister', False),
            (r"我\s*没有\s*妹妹", 'has_younger_sister', False),
            (r"没有\s*妹妹", 'has_younger_sister', False),
            (r"没\s*有\s*宠物|我\s*没有\s*宠物", 'has_pet', False),
        ]
        pos_patterns = [
            (r"我\s*有\s*哥哥", 'has_older_brother', True),
            (r"有\s*哥哥", 'has_older_brother', True),
            (r"我\s*有\s*弟弟", 'has_younger_brother', True),
            (r"有\s*弟弟", 'has_younger_brother', True),
            (r"我\s*有\s*姐姐", 'has_older_sister', True),
            (r"有\s*姐姐", 'has_older_sister', True),
            (r"我\s*有\s*妹妹", 'has_younger_sister', True),
            (r"有\s*妹妹", 'has_younger_sister', True),
            (r"我\s*有\s*宠物|有\s*宠物", 'has_pet', True),
        ]
        for pat, key, val in neg_patterns:
            if re.search(pat, t):
                facts[key] = val
        for pat, key, val in pos_patterns:
            if re.search(pat, t):
                facts[key] = val
    except Exception:
        pass
    return facts

def _first_question_for_unit(unit_id: str) -> str:
    """Provide a unit-appropriate first question (Chinese with pinyin prompt handled by model if needed)."""
    if unit_id == 'unit2':
        # Use the first ordered Unit 2 target question
        return _unit2_questions()[0]
    if unit_id == 'unit1':
        return "你的中文名字是什么？"
    if unit_id == 'unit3':
        # Simple schedule-related warm-up
        return "你今天上什么课？"
    # Generic fallback
    return "我们开始吧，你叫什么名字？"

def _greeting_for_unit(unit_id: str) -> str:
    """Provide a simple greeting to deliver after the instruction audio."""
    # Keep it simple and beginner-friendly; vary slightly
    options = [
        "你好！(Nǐ hǎo!)",
        "嗨！(Hài!)",
    ]
    try:
        return random.choice(options)
    except Exception:
        return options[0]

@app.route('/select')
def select_activity():
    # Accept unit query param and render a simple page to choose activity
    unit_id = request.args.get('unit', '')
    unit = get_unit(unit_id) if unit_id else None
    return render_template('select_activity.html', unit=unit, unit_id=unit_id)

@app.route('/matching')
def matching_page():
    unit_id = request.args.get('unit', '')
    unit = get_unit(unit_id) if unit_id else None
    return render_template('matching.html', unit=unit, unit_id=unit_id)

@app.route('/roleplay')
def roleplay_page():
    unit_id = request.args.get('unit', '')
    unit = get_unit(unit_id) if unit_id else None
    return render_template('roleplay.html', unit=unit, unit_id=unit_id)

@app.route('/units', methods=['GET'])
def list_units():
    return jsonify({
        'units': [
            { 'id': u['id'], 'title': u['title'], 'objectives': u.get('objectives', []) }
            for u in UNITS_CONFIG.get('units', [])
        ]
    })

# ---------- Health/Version Endpoints ----------
@app.route('/health', methods=['GET'])
def health():
    return jsonify({ 'status': 'ok', 'time': datetime.utcnow().isoformat() + 'Z' })

VERSION = os.getenv('VERSION', '1.0.0')
@app.route('/version', methods=['GET'])
def version():
    return jsonify({ 'version': VERSION })

# ---------- User Feedback Collection ----------
@app.route('/feedback', methods=['POST'])
def submit_feedback():
    """Collect user feedback for pilot study"""
    try:
        data = request.json or {}
        feedback_type = data.get('type', 'general')  # general, bug, feature, rating
        message = (data.get('message') or '').strip()
        rating = data.get('rating')  # 1-5 scale
        page = data.get('page', 'unknown')
        session_id = get_session_id()
        
        # Input validation
        if len(message) > 1000:
            return jsonify({'error': 'Feedback too long (max 1000 characters)'}), 400
        
        # Log feedback
        log_user_interaction('user_feedback', {
            'feedback_type': feedback_type,
            'message': message,
            'rating': rating,
            'page': page,
            'message_length': len(message)
        }, session_id=session_id)
        
        return jsonify({'status': 'success', 'message': 'Thank you for your feedback!'})
        
    except Exception as e:
        logger.error(f"Feedback submission error: {e}")
        return jsonify({'error': 'Failed to submit feedback'}), 500

# ---------- Character Matching Activity (adaptive, simple server-side) ----------
@app.route('/activity/matching/start', methods=['POST'])
def matching_start():
    data = request.json or {}
    unit_id = data.get('unitId')
    prev_missed = data.get('missed', [])  # list of hanzi strings to prioritize
    # Cap activity size to a reasonable range
    try:
        size = int(data.get('size') or 6)
    except Exception:
        size = 6
    size = max(2, min(size, 12))
    only_missed = bool(data.get('onlyMissed') or False)

    unit = get_unit(unit_id)
    if not unit:
        return jsonify({'error': 'Unknown unitId'}), 400

    vocab = unit.get('vocab', [])
    pool = vocab.copy()
    by_hanzi = {v['hanzi']: v for v in vocab}
    prioritized = [by_hanzi[h] for h in prev_missed if h in by_hanzi]
    others = [v for v in pool if v['hanzi'] not in set(prev_missed)]
    random.shuffle(others)
    if only_missed:
        # Serve only previously missed items; if none, fall back to a small random set
        round_items = prioritized if prioritized else others[:max(1, size)]
    else:
        round_items = (prioritized + others)[:max(2, size)]

    left = [
        { 'id': f"L{i}", 'hanzi': v['hanzi'] }
        for i, v in enumerate(round_items)
    ]
    right = [
        { 'id': f"R{i}", 'english': v['english'], 'pinyin': v['pinyin'], 'hanzi': v['hanzi'] }
        for i, v in enumerate(round_items)
    ]
    # Shuffle right side to create matching challenge
    random.shuffle(right)

    # Answer key maps left id to the english text of the correct right item
    key = { left[i]['id']: round_items[i]['english'] for i in range(len(round_items)) }

    return jsonify({
        'unitId': unit_id,
        'left': left,
        'right': right,
        'keyType': 'english',
        'instructions': unit.get('matching_prompt', ''),
    })

@app.route('/activity/matching/check', methods=['POST'])
def matching_check():
    data = request.json or {}
    unit_id = data.get('unitId')
    pairs = data.get('pairs', [])  # list of {leftId, leftHanzi, rightId, rightValue}
    session_id = get_session_id()
    
    # Log matching attempt
    log_user_interaction('matching_attempt', {
        'unit_id': unit_id,
        'num_pairs': len(pairs),
        'pairs_preview': pairs[:3] if pairs else []  # Log first 3 pairs for analysis
    }, session_id=session_id)
    # Basic input constraints
    if not isinstance(pairs, list) or len(pairs) > 24:
        return jsonify({'error': 'too many pairs'}), 400
    for p in pairs:
        if not isinstance(p, dict):
            return jsonify({'error': 'invalid pair format'}), 400
        for k in ('leftHanzi','rightValue'):
            v = (p.get(k) or '')
            if isinstance(v, str) and len(v) > 100:
                return jsonify({'error': 'value too long'}), 400
    mode = (data.get('mode') or 'english').lower()  # 'english' or 'pinyin'
    if not pairs:
        return jsonify({'error': 'pairs are required'}), 400

    unit = get_unit(unit_id)
    if not unit:
        return jsonify({'error': 'Unknown unitId'}), 400

    # Build ground truth mapping and vocab index
    vocab_index = build_vocab_index(unit)
    # Build a map from single character -> list of vocab words containing it (for association tips)
    char_to_words = {}
    for v in unit.get('vocab', []):
        hz = (v.get('hanzi') or '').strip()
        if not hz:
            continue
        for ch in set(hz):
            if ord(ch) < 0x3400:  # skip non-CJK basic chars as a heuristic
                continue
            char_to_words.setdefault(ch, []).append(hz)
    if mode == 'pinyin':
        truth = { v['hanzi']: v.get('pinyin', '') for v in unit.get('vocab', []) }
        expected_label = 'expectedPinyin'
    else:
        truth = { v['hanzi']: v.get('english', '') for v in unit.get('vocab', []) }
        expected_label = 'expectedEnglish'

    correct = []
    incorrect = []
    # Minimal radicals map for common beginner characters/words. For multi-character words, include key components.
    radicals_map = {
        '你': ['亻 (person)'], '您': ['亻 (person)', '心 (heart)'], '好': ['女 (woman)', '子 (child)'], '是': ['日 (sun)'],
        '我': ['戈 (halberd)'], '他': ['亻 (person)'], '她': ['女 (woman)'], '们': ['亻 (person)', '门 (door)'],
        '再': ['冂 (down box)'], '见': ['见 (see)'], '吗': ['口 (mouth)'], '不': ['一 (one)'], '姓': ['女 (woman)'],
        '叫': ['口 (mouth)'], '的': ['白 (white)'], '英': ['艹 (grass)'], '文': ['文 (literature)'], '中': ['口 (mouth)'],
        '名': ['夕 (evening)', '口 (mouth)'], '一': ['一 (one)'], '二': ['二 (two)'], '三': ['一 (one)'], '四': ['囗 (enclosure)'],
        '五': ['二 (two)'], '六': ['亠 (lid)'], '七': ['一 (one)'], '八': ['八 (eight)'], '九': ['乙 (second)'],
        '大': ['大 (big)'], '小': ['小 (small)'], '人': ['人 (person)'], '女': ['女 (woman)'], '男': ['田 (field)', '力 (power)'],
        '口': ['口 (mouth)'], '家': ['宀 (roof)'], '学': ['子 (child)'], '生': ['生 (life)'], '老': ['耂 (old)'], '师': ['巾 (cloth)'],
    }
    # Common glosses for shared characters (helps students notice patterns)
    shared_char_gloss = {
        '学': 'study/learn', '生': 'student/life', '友': 'friend', '室': 'room', '同': 'same/together',
        '中': 'middle/China', '文': 'language/writing', '英': 'English/heroic', '名': 'name',
        '电': 'electric', '话': 'speech/talk', '号': 'number/day', '日': 'day/sun', '人': 'person',
        '老': 'old/teacher (in 老师)', '师': 'teacher', '们': 'plural marker', '国': 'country',
        '校': 'school', '课': 'class/lesson', '工': 'work', '打': 'to hit/do (in 打工)', '天': 'day/sky'
    }
    for p in pairs:
        hanzi = p.get('leftHanzi')
        chosen = p.get('rightValue')
        is_ok = truth.get(hanzi) == chosen
        item = {
            'leftHanzi': hanzi,
            'rightValue': chosen,
            expected_label: truth.get(hanzi)
        }
        if not is_ok:
            # Enrich incorrect items with pinyin, english meaning, and radicals, plus a short explanation tip
            vi = vocab_index.get(hanzi, {})
            item['pinyin'] = vi.get('pinyin')
            item['meaning'] = vi.get('english')
            # Gender annotation for pronouns sharing the same pinyin
            if hanzi == '她' and item.get('pinyin'):
                item['pinyinDisplay'] = f"{item['pinyin']} (female)"
            elif hanzi == '他' and item.get('pinyin'):
                item['pinyinDisplay'] = f"{item['pinyin']} (male)"
            # Collect radicals for each character in the hanzi string
            chars = list(hanzi)
            rads = []
            radicals_by_char = []
            for ch in chars:
                ch_rads = radicals_map.get(ch, [])
                rads.extend(ch_rads)
                if ch_rads:
                    radicals_by_char.append({ 'char': ch, 'radicals': ch_rads })
            if rads:
                item['radicals'] = rads
            if radicals_by_char:
                item['radicalsByChar'] = radicals_by_char
            # Character association tips: show other vocab that share a character and explain its idea
            assoc_tips = []
            for ch in set(chars):
                words = [w for w in char_to_words.get(ch, []) if w != hanzi]
                if words:
                    examples = ", ".join(words[:3])
                    gloss = shared_char_gloss.get(ch)
                    if gloss:
                        assoc_tips.append(f"{ch} appears in {examples} — think '{gloss}'.")
                    else:
                        assoc_tips.append(f"{ch} appears in {examples}.")
            if assoc_tips:
                item['associationTips'] = assoc_tips
            # Explanation: why it's wrong and quick mnemonic
            expected_val = truth.get(hanzi) or ''
            # Explanation
            if mode == 'pinyin':
                item['explanation'] = (
                    f"Pinyin for {hanzi} is '{expected_val}'. You chose '{chosen}'. Notice tone marks and initials/finals. "
                    f"Try saying it with the tones from the pinyin shown."
                )
            else:
                item['explanation'] = (
                    f"Meaning for {hanzi} is '{expected_val}'. You chose '{chosen}'. Focus on key radical(s) to recall meaning."
                )
            # Natural sample sentence: try AI (constrained to unit vocab) then fallback
            ai_sample = generate_ai_sample_sentence(unit, hanzi, vi.get('pinyin',''), vi.get('english','')) if client is not None else None
            item['sample'] = ai_sample or generate_sample_sentence(unit_id, hanzi, vi.get('pinyin',''), vi.get('english',''))
        (correct if is_ok else incorrect).append(item)

    accuracy = round(100 * len(correct) / max(1, len(pairs)))
    next_size = 6 if accuracy >= 70 else 4
    missed = [item['leftHanzi'] for item in incorrect]

    # Adaptive, encouraging feedback (fallback)
    if accuracy >= 90:
        tone = "Fantastic work! 继续加油 (jìxù jiāyóu) — keep it up!"
    elif accuracy >= 70:
        tone = "Nice progress! 稍微复习一下就更棒了 — a little review will make it perfect."
    else:
        tone = "Good effort! 别担心 (bié dānxīn) — mistakes help you learn. Let's focus on the tricky ones."
    feedback = f"Accuracy: {accuracy}%. {tone}"

    # Generate AI-based overall feedback and memorization tips
    aiFeedback = None
    if client is not None:
        ai = generate_ai_matching_feedback(unit, accuracy, incorrect, mode)
        if ai and isinstance(ai, dict):
            aiFeedback = ai.get('overall') or feedback
            tips_map = ai.get('tipsByHanzi') or {}
            # Attach memo tips to incorrect items
            for it in incorrect:
                hz = it.get('leftHanzi')
                tips = tips_map.get(hz)
                if isinstance(tips, list) and tips:
                    it['memoTips'] = tips[:3]

    return jsonify({
        'accuracy': accuracy,
        'correct': correct,
        'incorrect': incorrect,
        'missed': missed,
        'nextSize': next_size,
        'feedback': feedback,
        'aiFeedback': aiFeedback,
        'mode': mode,
    })

# ---------- Role Play Activity (unit-aware, OpenAI-assisted) ----------
@app.route('/activity/roleplay/start', methods=['POST'])
def roleplay_start():
    try:
        data = request.json or {}
        unit_id = data.get('unitId')
        unit = get_unit(unit_id)
        if not unit:
            return jsonify({'error': 'Unknown unitId'}), 400

        # Do not generate or send instruction via chatbot; UI will display English instructions.
        first_q = _first_question_for_unit(unit_id)
        greet = _greeting_for_unit(unit_id)
        opening = (greet or '')
        if first_q:
            opening = (opening + ("\n" if opening else "") + first_q)
        return jsonify({ 'unitId': unit_id, 'instruction': '', 'greeting': greet, 'firstQuestion': first_q, 'opening': opening })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# On-demand English gloss for an assistant line (used when user clicks 'Show English')
@app.route('/activity/roleplay/translate', methods=['POST'])
def roleplay_translate():
    try:
        if client is None:
            return jsonify({'error': 'OpenAI API key is not configured.'}), 500
        data = request.json or {}
        text = (data.get('text') or '').strip()
        if not text:
            return jsonify({'error': 'text is required'}), 400
        # Ask for a very short English gloss suitable for beginners
        sys = { 'role': 'system', 'content': (
            "You are Emily (李爱), providing a brief English gloss for a beginner. "
            "Output ONE very short, clear English line that conveys the meaning of the provided Chinese (with pinyin). "
            "Do not add extra commentary or Chinese back."
        )}
        user = { 'role': 'user', 'content': text }
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[sys, user],
            temperature=0.2,
            max_tokens=80
        )
        gloss = (resp.choices[0].message.content or '').strip()
        return jsonify({ 'english': gloss })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/activity/roleplay/turn', methods=['POST'])
def roleplay_turn():
    try:
        if client is None:
            return jsonify({'error': 'OpenAI API key is not configured.'}), 500
        
        data = request.json or {}
        unit_id = data.get('unitId')
        user_message = (data.get('message') or '').strip()
        student_name = (data.get('studentName') or '').strip()
        history = data.get('history', [])  # Add missing history variable
        session_id = get_session_id()
        
        # Log roleplay interaction
        log_user_interaction('roleplay_turn', {
            'unit_id': unit_id,
            'message_length': len(user_message),
            'has_student_name': bool(student_name),
            'user_message': user_message[:100] + '...' if len(user_message) > 100 else user_message  # Truncate for privacy
        }, session_id=session_id)
        
        # Input validation
        if len(user_message) > Config.MAX_TURN_CHARS:
            return jsonify({'error': f'Message too long (max {Config.MAX_TURN_CHARS} characters)'}), 400
        if not user_message:
            return jsonify({'error': 'message is required'}), 400
        unit = get_unit(unit_id)
        if not unit:
            return jsonify({'error': 'Unknown unitId'}), 400

        # Short-circuit: if the student says goodbye, reply with farewell + reminder
        def _said_goodbye(text: str) -> bool:
            tokens = ['再见', '拜拜', '回头见', 'bye', 'Bye', 'goodbye']
            return any(tok in (text or '') for tok in tokens)
        if _said_goodbye(user_message):
            reply = "再见！(Zàijiàn!) 记得查看反馈并下载你的学习证明（学习总结/徽章）。"
            return jsonify({ 'reply': reply })

        rp_prompt = unit.get('roleplay_prompt', '')
        # Reuse the roleplay-specific persona and rules
        persona_rules = (
            "Persona: You are the student's Mandarin language partner (语言朋友), NEVER say you are an AI. "
            "Name 李爱 (Emily), from Beijing, Chinese teacher, phone 1358679042. Friend 高山 (Gordon), American doctor; he is tall and handsome; you are short.\n"
            "During the role play, answer in Chinese with pinyin in parentheses, no English unless asked for help/slow-down/they don't understand. "
            "Turn order: The student should ask questions first. Only respond after they ask. Do not guide them or ask any questions. "
            "Disclosure constraint: Only reveal information that the student explicitly asks for. Do NOT volunteer extra details. Keep answers BRIEF and on-topic. "
            "Minimal answers examples: If asked ‘你家有几口人？’, reply only ‘我家有五口人。(Wǒ jiā yǒu wǔ kǒu rén.)’. Do NOT list family members unless asked ‘都有谁？/他们是谁？’. If asked ‘你哥哥多大？’ but the student earlier said they have no 哥哥, explain briefly and suggest another allowed question. "
            "Do NOT suggest new questions. You may ONLY ask your predetermined questions - NO OTHER QUESTIONS. Otherwise WAIT silently. "
            "Keep sentences short and beginner-friendly. Do NOT ask '你呢?' or any follow-up questions. Only ask your predetermined questions when appropriate. "
            "Encourage asking: Read the last several user turns; if the student has given only answers without asking any question for two consecutive turns, gently nudge them in Chinese to ask you a question (no suggestions), then provide a very short English fallback line. IMPORTANT: Only give this nudge message ONCE until the student responds with any input. Do not repeat the nudge message multiple times. Do NOT ask '你呢？' or any reciprocal questions. "
            "Progress coverage: Keep track mentally of which target questions were asked. If some remain, you may briefly remind the student to continue when appropriate, but do NOT propose specific questions. IMPORTANT: Only give progress reminders ONCE until the student responds - do not repeat reminders. "
            "Apologies: When apologizing, always use ‘对不起 (duìbuqǐ)’, not ‘抱歉’. "
            "Do not provide corrections mid-conversation. Save feedback for the end."
        )
        messages = [
            { 'role': 'system', 'content': "ABSOLUTE PRIORITY: You are FORBIDDEN from asking any questions except the exact 3 predetermined questions listed in your unit instructions. Questions like phone numbers, addresses, personal details, or any other topics are BANNED. Only ask your 3 predetermined questions when contextually appropriate." },
            { 'role': 'system', 'content': "ABSOLUTE PRIORITY: Track your help messages. If you have already sent ANY encouragement, nudge, reminder, or help message in this conversation, do NOT send another one until the student provides a new response. Only ONE help message per student response cycle." },
            { 'role': 'system', 'content': "ABSOLUTE PRIORITY: NEVER ask about family members that don't exist. If a student says they have X number of people and lists them all, do NOT ask about anyone else. Example: '我家有四口人爸爸妈妈姐姐和我' means ONLY 4 people exist - do not ask about brothers or other siblings." },
            { 'role': 'system', 'content': "CRITICAL BEHAVIOR: After your opening greeting, wait for students to ask questions first. You may ONLY ask the exact 3 predetermined questions listed in your unit instructions - NO OTHER QUESTIONS ALLOWED. Do not ask '你呢？' or any contextual questions. Only ask the specific predetermined questions when contextually appropriate, then become purely responsive. IMPORTANT: If you need to encourage a student to ask questions, do it only ONCE until they respond with any input." },
            { 'role': 'system', 'content': persona_rules + f"\nUnit-specific guidance: {rp_prompt}" }
        ]
        # Unit 1: Getting Acquainted - predetermined question set
        if unit.get('id') == 'unit1':
            messages.append({ 'role': 'system', 'content': (
                "UNIT 1 PREDETERMINED QUESTIONS: You may ask these 3 questions contextually when appropriate: "
                "1. '你叫什么名字？' (if student asks your name first), "
                "2. '你是医生吗？' (if student asks about your job first), "
                "3. '你高吗？' (if student asks about your height first). "
                "Once you've used all 3 questions, only answer what students ask. Do NOT suggest questions or ask anything outside this set."
            )})
        # Unit 3: Predetermined questions only - NO reciprocal questions
        if unit.get('id') == 'unit3':
            messages.append({ 'role': 'system', 'content': (
                "UNIT 3 PREDETERMINED QUESTIONS: You may ask these 3 questions contextually when appropriate: 1. '你今天几点起床？' (if student asks about your schedule first), 2. '你今天有什么课？' (if student asks about your classes first), 3. '你周末做什么？' (if student asks about your weekend activities first). Once you've used all 3 questions, only answer what students ask. Do NOT suggest questions or ask anything outside this set. (no phrases like ‘你可以问我…’). "
                "Answer BRIEFLY exactly what the student asks. You may ONLY ask your 3 predetermined questions - NO OTHER QUESTIONS. Do NOT ask '你呢？' or any contextual questions. IMPORTANT: Any encouragement messages should only be sent ONCE until student responds."
            )})
        # For Unit 2, inject strict sequencing controller and progress hints
        if unit.get('id') == 'unit2':
            # Unit 2: Do not suggest next questions; only answer briefly and wait
            messages.append({ 'role': 'system', 'content': (
                "UNIT 2 PREDETERMINED QUESTIONS: You may ask these 3 questions contextually when appropriate: 1. '你家有几口人？' (if student asks about your family first), 2. '你爸爸妈妈多少岁了？' (if student ask about your parents' ages first), 3. '你多大？' (if student asks about your age first). Once you've used all 3 questions, only answer what students ask. Follow family logic rules (no phrases like ‘你可以问我…’). "
                "Answer BRIEFLY exactly what the student asks. You may ONLY ask your 3 predetermined questions - NO OTHER QUESTIONS. CRITICAL FAMILY LOGIC: When students describe their family, listen to EXACTLY who they mention. Common examples: (1) '我家有三口人爸爸妈妈和我' = NO siblings, only ask about parents. (2) '我家有四口人爸爸妈妈哥哥和我' = ONE older brother only, don't ask about sisters or younger brothers. (3) '我家有五口人爸爸妈妈哥哥妹妹和我' = ONE older brother and ONE younger sister only. (4) '我家有六口人爸爸妈妈爷爷奶奶姐姐和我' = grandparents and ONE older sister only. Always count the total number they give and match it exactly to who they list - never ask about family members they didn't mention. Do NOT ask '你呢？' for factual information like ages or dates. IMPORTANT: Any encouragement messages should only be sent ONCE until student responds."
            )})
            unit2_qs = _unit2_questions()
            prog = _unit2_progress_hint(history)
            asked_idx = prog.get('asked_indices', [])
            next_idx = prog.get('next_index', 0)
            remaining = prog.get('remaining', unit2_qs)
            # Filter remaining questions using inferred student facts
            facts = _infer_student_facts(history)
            # Heuristic: detect if student explicitly has no siblings (only parents and self)
            def _history_text(msgs):
                try:
                    return "\n".join([ (m.get('content') or '') for m in msgs if m.get('role')=='user' ])
                except Exception:
                    return ''
            htxt = _history_text(history)
            # Normalize spacing for robust matching
            norm = htxt.replace('\u3000',' ').replace('\n',' ').strip()
            no_siblings_phrases = [
                '没有兄弟姐妹', '没兄弟姐妹', '我是独生子女', '没有哥哥', '没有姐姐', '没有弟弟', '没有妹妹'
            ]
            said_no_siblings = any(p in norm for p in no_siblings_phrases)
            said_three = ('三口人' in norm) or ('三个人' in norm)
            said_four = ('四口人' in norm) or ('四个人' in norm)
            said_five = ('五口人' in norm) or ('五个人' in norm)
            said_six = ('六口人' in norm) or ('六个人' in norm)
            mentions_parents_and_me = ('爸爸' in norm and '妈妈' in norm and '我' in norm)
            only_parents_and_me = mentions_parents_and_me and said_three and not any(w in norm for w in ['哥哥','姐姐','弟弟','妹妹'])
            
            # 4-person family patterns
            four_person_only_older_sister = said_four and mentions_parents_and_me and ('姐姐' in norm) and not any(w in norm for w in ['弟弟','哥哥','妹妹'])
            four_person_only_younger_sister = said_four and mentions_parents_and_me and ('妹妹' in norm) and not any(w in norm for w in ['弟弟','哥哥','姐姐'])
            four_person_only_older_brother = said_four and mentions_parents_and_me and ('哥哥' in norm) and not any(w in norm for w in ['弟弟','姐姐','妹妹'])
            four_person_only_younger_brother = said_four and mentions_parents_and_me and ('弟弟' in norm) and not any(w in norm for w in ['哥哥','姐姐','妹妹'])
            
            # 5-person family patterns (exactly two siblings)
            five_person_older_bro_older_sis = said_five and mentions_parents_and_me and ('哥哥' in norm) and ('姐姐' in norm) and not any(w in norm for w in ['弟弟','妹妹'])
            five_person_older_bro_younger_bro = said_five and mentions_parents_and_me and ('哥哥' in norm) and ('弟弟' in norm) and not any(w in norm for w in ['姐姐','妹妹'])
            five_person_older_sis_younger_sis = said_five and mentions_parents_and_me and ('姐姐' in norm) and ('妹妹' in norm) and not any(w in norm for w in ['哥哥','弟弟'])
            five_person_younger_bro_younger_sis = said_five and mentions_parents_and_me and ('弟弟' in norm) and ('妹妹' in norm) and not any(w in norm for w in ['哥哥','姐姐'])
            five_person_older_bro_younger_sis = said_five and mentions_parents_and_me and ('哥哥' in norm) and ('妹妹' in norm) and not any(w in norm for w in ['弟弟','姐姐'])
            five_person_older_sis_younger_bro = said_five and mentions_parents_and_me and ('姐姐' in norm) and ('弟弟' in norm) and not any(w in norm for w in ['哥哥','妹妹'])
            
            # 6-person family patterns (exactly three siblings) - Common combinations
            six_person_all_older = said_six and mentions_parents_and_me and ('哥哥' in norm) and ('姐姐' in norm) and not any(w in norm for w in ['弟弟','妹妹'])  # Only older siblings
            six_person_all_younger = said_six and mentions_parents_and_me and ('弟弟' in norm) and ('妹妹' in norm) and not any(w in norm for w in ['哥哥','姐姐'])  # Only younger siblings
            six_person_older_bro_both_sis = said_six and mentions_parents_and_me and ('哥哥' in norm) and ('姐姐' in norm) and ('妹妹' in norm) and ('弟弟' not in norm)  # Older bro + both sisters
            six_person_older_sis_both_bro = said_six and mentions_parents_and_me and ('姐姐' in norm) and ('哥哥' in norm) and ('弟弟' in norm) and ('妹妹' not in norm)  # Older sis + both brothers
            six_person_younger_bro_both_sis = said_six and mentions_parents_and_me and ('弟弟' in norm) and ('姐姐' in norm) and ('妹妹' in norm) and ('哥哥' not in norm)  # Younger bro + both sisters
            six_person_younger_sis_both_bro = said_six and mentions_parents_and_me and ('妹妹' in norm) and ('哥哥' in norm) and ('弟弟' in norm) and ('姐姐' not in norm)  # Younger sis + both brothers
            six_person_both_older_younger_sis = said_six and mentions_parents_and_me and ('哥哥' in norm) and ('姐姐' in norm) and ('妹妹' in norm) and ('弟弟' not in norm)  # Both older siblings + younger sister
            no_siblings = said_no_siblings or only_parents_and_me or (facts.get('has_siblings') is False)
            def allow(q: str) -> bool:
                # Determine what siblings DON'T exist based on family composition
                no_older_brother = (four_person_only_older_sister or four_person_only_younger_sister or 
                                   four_person_only_younger_brother or five_person_older_sis_younger_sis or 
                                   five_person_younger_bro_younger_sis or five_person_older_sis_younger_bro or
                                   six_person_all_younger or six_person_younger_bro_both_sis)
                no_younger_brother = (four_person_only_older_sister or four_person_only_younger_sister or 
                                     four_person_only_older_brother or five_person_older_bro_older_sis or 
                                     five_person_older_sis_younger_sis or five_person_older_bro_younger_sis or
                                     six_person_all_older or six_person_older_bro_both_sis or six_person_both_older_younger_sis)
                no_older_sister = (four_person_only_younger_sister or four_person_only_older_brother or 
                                  four_person_only_younger_brother or five_person_older_bro_younger_bro or 
                                  five_person_younger_bro_younger_sis or five_person_older_bro_younger_sis or
                                  six_person_all_younger or six_person_younger_sis_both_bro)
                no_younger_sister = (four_person_only_older_sister or four_person_only_older_brother or 
                                    four_person_only_younger_brother or five_person_older_bro_older_sis or 
                                    five_person_older_bro_younger_bro or five_person_older_sis_younger_bro or
                                    six_person_all_older or six_person_older_sis_both_bro)
                
                # Apply filtering rules
                if ('哥哥' in q) and ((facts.get('has_older_brother') is False) or no_older_brother):
                    return False
                if ('弟弟' in q) and ((facts.get('has_younger_brother') is False) or no_younger_brother):
                    return False
                if ('姐姐' in q) and ((facts.get('has_older_sister') is False) or no_older_sister):
                    return False
                if ('妹妹' in q) and ((facts.get('has_younger_sister') is False) or no_younger_sister):
                    return False
                # If the student said they have only parents and self (or no siblings), skip any sibling-related question
                if no_siblings and any(ch in q for ch in ['哥哥','姐姐','弟弟','妹妹','兄弟姐妹']):
                    return False
                # Skip pet Q if said no pet
                if ('宠物' in q) and (facts.get('has_pet') is False):
                    return False
                return True
            remaining_filtered = [q for q in remaining if allow(q)] or remaining
            ctl = (
                "STRICT COMPLIANCE (Unit 2): Only cover the following questions, in order. Do NOT suggest questions. "
                "After answering, you may ONLY ask your predetermined questions - NO OTHER QUESTIONS; otherwise WAIT. If the student deviates, briefly remind them to continue.\n"
                "Ordered questions (filtering out items the student said they don't have, e.g., siblings/pet):\n- " + "\n- ".join(unit2_qs) + "\n" +
                f"Progress hint — asked indices: {asked_idx}; next_index: {next_idx}; next_question: {unit2_qs[next_idx] if 0 <= next_idx < len(unit2_qs) else unit2_qs[-1]}; remaining_count: {len(remaining_filtered)}. "
                f"Filtered remaining: {remaining_filtered}. "
                "If the student states they don't have a family member (e.g., ‘我没有哥哥’), do not ask about that member (e.g., skip ‘你的哥哥多大？’ or ‘你的哥哥也是老师吗？’). "
                "Keep each reply concise and supportive; wait for the student to ask the next question."
            )
            messages.append({ 'role': 'system', 'content': ctl })
        # Add prior exchanges
        for m in history:
            if m.get('role') in ('user','assistant') and m.get('content'):
                messages.append({ 'role': m['role'], 'content': m['content'] })
        messages.append({ 'role': 'user', 'content': user_message })

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.6,
            max_tokens=300
        )
        reply = resp.choices[0].message.content
        reply = normalize_apologies(reply)
        return jsonify({ 'reply': reply })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# End-of-conversation feedback for roleplay
@app.route('/activity/roleplay/feedback', methods=['POST'])
def roleplay_feedback():
    try:
        if client is None:
            return jsonify({'error': 'OpenAI API key is not configured.'}), 500
        data = request.json or {}
        unit_id = data.get('unitId')
        history = data.get('history', [])  # list of {role, content}
        if not unit_id:
            return jsonify({'error': 'unitId is required'}), 400
        unit = get_unit(unit_id)
        if not unit:
            return jsonify({'error': 'Unknown unitId'}), 400
        # Build a compact transcript for analysis
        convo = []
        for m in history:
            r = m.get('role'); c = (m.get('content') or '').strip()
            if r in ('user','assistant') and c:
                convo.append(f"{r}: {c}")
        transcript = "\n".join(convo[-30:])  # last 30 turns max

        sys = {
            'role': 'system',
            'content': (
                "You are Emily (李爱), a supportive Mandarin teacher for beginners. "
                "Provide END-OF-CONVERSATION feedback only. "
                "Write in English, but include short Chinese examples with pinyin in parentheses where helpful. "
                "Keep feedback concise and encouraging. Focus on: (1) grammar accuracy with simple fixes, (2) pronunciation notes for tones/initials, (3) 2-3 suggested practice sentences relevant to Unit 1 targets (name, surname, English name, profession, phone number, how are you, height/looks, friend info). "
                "Do NOT list every minor issue; prioritize the most helpful tips for a beginner."
            )
        }
        user = {
            'role': 'user',
            'content': (
                f"Unit: {unit.get('title')}\n"
                "Here is the transcript of our role play. Please give brief, encouraging feedback as specified.\n\n" + transcript
            )
        }
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[sys, user],
            temperature=0.4,
            max_tokens=400
        )
        fb = resp.choices[0].message.content
        return jsonify({ 'feedback': fb })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

 

# ---------- AI Badge Generator ----------
@app.route('/badge', methods=['GET'])
def generate_badge():
    try:
        if client is None:
            return jsonify({'error': 'OpenAI API key is not configured.'}), 500
        name = (request.args.get('name') or '').strip() or 'Great Wall'
        palette_name = (request.args.get('palette') or '').strip().lower()
        force = (request.args.get('force') or '').strip() in ('1','true','yes')
        palettes = {
            'peach': ('#F59E8B', '#FDE2D6', '#7A3415'),
            'teal': ('#0D9488', '#99F6E4', '#115E59'),
            'violet': ('#7C3AED', '#DDD6FE', '#4C1D95'),
            'indigo': ('#6366F1', '#E0E7FF', '#312E81'),
            'emerald': ('#10B981', '#D1FAE5', '#065F46'),
            'rose': ('#E11D48', '#FFE4E6', '#881337'),
            'amber': ('#F59E0B', '#FEF3C7', '#78350F'),
            'slate': ('#64748B', '#E2E8F0', '#1F2937'),
            'cyan': ('#06B6D4', '#CFFAFE', '#164E63'),
            'lime': ('#84CC16', '#ECFCCB', '#365314'),
        }
        import random as _r, re
        if palette_name in palettes:
            primary, light, dark = palettes[palette_name]
        else:
            primary, light, dark = palettes[_r.choice(list(palettes.keys()))]

        # Filesystem cache setup
        cache_dir = Path(__file__).parent / 'cache' / 'badges'
        cache_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_').lower() or 'landmark'
        safe_palette = re.sub(r'[^A-Za-z0-9]+', '_', (palette_name or 'random')).strip('_').lower()
        cache_file = cache_dir / f"{safe_name}_{safe_palette}.svg"

        # Serve from cache when available unless forced
        if cache_file.exists() and not force:
            try:
                svg_cached = cache_file.read_text(encoding='utf-8')
                return Response(svg_cached, mimetype='image/svg+xml')
            except Exception:
                pass

        # Ask OpenAI for a compact silhouette path(s) in JSON
        sys = { 'role':'system', 'content': (
            "You are an SVG designer. Output ONLY JSON."
            " Create 2-5 layered silhouette paths for a Chinese landmark badge icon: include an outer contour and 1-3 inner details (roof tiers, arches, crenellations)."
            " Prefer curved commands (C/Q) mixed with M/L for realism, but keep it simple and clean (no noise)."
            " Use SVG path 'd' strings scaled to fit within ~200x160 box centered at (0,0)."
            " Avoid text, gradients, filters, transforms, or colors. Only provide path 'd' strings."
        )}
        user = { 'role': 'user', 'content': (
            "Landmark: " + name + ". Return JSON {\"paths\":[{\"d\":\"...\"}, ...]} with 2-5 items."
        )}
        resp = client.chat.completions.create(
            model='gpt-4o',
            messages=[sys, user],
            temperature=0.2,
            max_tokens=500
        )
        txt = resp.choices[0].message.content or ''
        s = txt.find('{'); e = txt.rfind('}')
        paths = []
        if s != -1 and e != -1 and e > s:
            try:
                payload = json.loads(txt[s:e+1])
                arr = payload.get('paths') or []
                for p in arr:
                    d = (p.get('d') or '').strip()
                    if d:
                        paths.append(d)
            except Exception:
                pass
        if not paths:
            return jsonify({'error': 'Could not parse AI badge paths'}), 502

        # Compose final SVG badge with our palette and label
        svg = f"""<?xml version='1.0' encoding='UTF-8'?>
<svg xmlns='http://www.w3.org/2000/svg' width='400' height='400' viewBox='0 0 400 400'>
  <defs>
    <radialGradient id='g' cx='50%' cy='40%' r='65%'><stop offset='0%' stop-color='{light}'/><stop offset='100%' stop-color='{primary}'/></radialGradient>
  </defs>
  <circle cx='200' cy='200' r='180' fill='url(#g)' stroke='{dark}' stroke-width='6'/>
  <circle cx='200' cy='200' r='145' fill='rgba(255,255,255,0.15)'/>
  <g transform='translate(200,220)'>
"""
        for d in paths:
            svg += f"    <path d='{d}' fill='{dark}'/>\n"
        svg += f"""  </g>
  <text x='200' y='360' text-anchor='middle' font-family='sans-serif' font-size='18' fill='{dark}'>{name}</text>
</svg>"""
        # Write to cache (best-effort)
        try:
            cache_file.write_text(svg, encoding='utf-8')
        except Exception:
            pass
        return Response(svg, mimetype='image/svg+xml')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/chat', methods=['POST'])
def chat():
    try:
        if client is None:
            return jsonify({'error': 'OpenAI API key is not configured. Please set OPENAI_API_KEY in your .env.'}), 500

        data = request.json or {}
        user_message = data.get('message', '').strip()
        conversation_history = data.get('conversation', []) or []

        if not user_message:
            return jsonify({'error': 'Message is required'}), 400
        
        # Format messages for OpenAI
        messages = [SYSTEM_MESSAGE]
        
        # Add conversation history
        for msg in conversation_history:
            role = 'user' if msg.get('role') == 'user' else 'assistant'
            content = msg.get('content', '')
            if content:
                messages.append({"role": role, "content": content})
        
        # Add current user message
        messages.append({"role": "user", "content": user_message})
        
        # Call OpenAI API using new client
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )
        
        # Get the assistant's response
        assistant_response = response.choices[0].message.content
        
        return jsonify({'response': assistant_response})
        
    except httpx.TimeoutException as e:
        msg = 'Network timeout contacting OpenAI. Check your internet connection or firewall.'
        print(f"Timeout in chat endpoint: {e}")
        return jsonify({'error': msg}), 504
    except httpx.ConnectError as e:
        msg = 'Cannot reach OpenAI. No internet route or DNS blocked.'
        print(f"ConnectError in chat endpoint: {e}")
        return jsonify({'error': msg}), 503
    except Exception as e:
        print(f"Error in chat endpoint: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Text-to-Speech endpoint (server-side)
@app.route('/tts', methods=['POST'])
def tts():
    try:
        if client is None:
            return jsonify({'error': 'OpenAI API key is not configured. Please set OPENAI_API_KEY in your .env.'}), 500

        data = request.json or {}
        text = (data.get('text') or '').strip()
        if len(text) > Config.MAX_TTS_CHARS:
            return jsonify({'error': f'text too long (>{Config.MAX_TTS_CHARS} chars)'}), 400
        # Enforce a single voice for consistency across the app
        voice = 'alloy'
        if not text:
            return jsonify({'error': 'text is required'}), 400

        # Normalize text for telephony: read long digit sequences with '1' as 幺 (yāo)
        def _telephony_normalize(s: str) -> str:
            if not s:
                return s
            # Map digits to Chinese telephony numerals (1 -> 幺)
            digit_map = {'0':'零','1':'幺','2':'二','3':'三','4':'四','5':'五','6':'六','7':'七','8':'八','9':'九'}
            def repl(match: re.Match) -> str:
                seq = match.group(0)
                # Convert each digit and separate with spaces for clarity
                converted = ' '.join(digit_map.get(ch, ch) for ch in seq)
                return converted
            # Only convert long sequences (e.g., phone numbers). Threshold: 6+ digits.
            return re.sub(r"\d{6,}", repl, s)

        text = _telephony_normalize(text)

        # Create speech audio from text
        # Using streaming response to avoid buffering large payloads in memory
        with client.audio.speech.with_streaming_response.create(
            model="gpt-4o-mini-tts",
            voice=voice,
            input=text,
            response_format="mp3",
        ) as stream:
            audio_bytes = stream.read()

        return Response(audio_bytes, mimetype='audio/mpeg')

    except httpx.TimeoutException as e:
        msg = 'Network timeout contacting OpenAI TTS. Check your internet connection or firewall.'
        print(f"Timeout in tts endpoint: {e}")
        return jsonify({'error': msg}), 504
    except httpx.ConnectError as e:
        msg = 'Cannot reach OpenAI TTS. No internet route or DNS blocked.'
        print(f"ConnectError in tts endpoint: {e}")
        return jsonify({'error': msg}), 503
    except Exception as e:
        print(f"Error in tts endpoint: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Bind to a dedicated port to avoid conflicts and make frontend relative calls work
    app.run(host='127.0.0.1', port=Config.PORT, debug=(Config.ENV != 'production'))
