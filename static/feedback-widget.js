/**
 * Simple feedback widget for pilot study data collection
 * Add this to any page where you want to collect user feedback
 */

class FeedbackWidget {
    constructor() {
        this.sessionId = this.getOrCreateSessionId();
        this.isVisible = false;
        this.createWidget();
        this.attachEventListeners();
    }

    getOrCreateSessionId() {
        let sessionId = sessionStorage.getItem('mandarin-session-id');
        if (!sessionId) {
            sessionId = 'sess_' + Math.random().toString(36).substr(2, 9);
            sessionStorage.setItem('mandarin-session-id', sessionId);
        }
        return sessionId;
    }

    createWidget() {
        // Create floating feedback button
        const button = document.createElement('button');
        button.id = 'feedback-btn';
        button.innerHTML = '💬 Feedback';
        button.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            background: #3B82F6;
            color: white;
            border: none;
            border-radius: 25px;
            padding: 12px 20px;
            font-size: 14px;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            z-index: 1000;
            transition: all 0.3s ease;
        `;
        
        // Create feedback modal
        const modal = document.createElement('div');
        modal.id = 'feedback-modal';
        modal.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            display: none;
            justify-content: center;
            align-items: center;
            z-index: 1001;
        `;

        modal.innerHTML = `
            <div style="background: white; border-radius: 12px; padding: 24px; max-width: 400px; width: 90%; max-height: 80vh; overflow-y: auto;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <h3 style="margin: 0; color: #1F2937;">Share Your Feedback</h3>
                    <button id="feedback-close" style="background: none; border: none; font-size: 24px; cursor: pointer; color: #6B7280;">&times;</button>
                </div>
                
                <form id="feedback-form">
                    <div style="margin-bottom: 16px;">
                        <label style="display: block; margin-bottom: 8px; font-weight: 500; color: #374151;">How would you rate your experience?</label>
                        <div id="rating-stars" style="display: flex; gap: 4px; margin-bottom: 8px;">
                            ${[1,2,3,4,5].map(i => `<span class="star" data-rating="${i}" style="font-size: 24px; cursor: pointer; color: #D1D5DB;">⭐</span>`).join('')}
                        </div>
                    </div>
                    
                    <div style="margin-bottom: 16px;">
                        <label style="display: block; margin-bottom: 8px; font-weight: 500; color: #374151;">Feedback Type</label>
                        <select id="feedback-type" style="width: 100%; padding: 8px; border: 1px solid #D1D5DB; border-radius: 6px;">
                            <option value="general">General Feedback</option>
                            <option value="bug">Bug Report</option>
                            <option value="feature">Feature Request</option>
                            <option value="usability">Usability Issue</option>
                        </select>
                    </div>
                    
                    <div style="margin-bottom: 16px;">
                        <label style="display: block; margin-bottom: 8px; font-weight: 500; color: #374151;">Your Message</label>
                        <textarea id="feedback-message" placeholder="Tell us about your experience..." style="width: 100%; height: 100px; padding: 8px; border: 1px solid #D1D5DB; border-radius: 6px; resize: vertical;" maxlength="1000"></textarea>
                        <div style="text-align: right; font-size: 12px; color: #6B7280; margin-top: 4px;">
                            <span id="char-count">0</span>/1000
                        </div>
                    </div>
                    
                    <div style="display: flex; gap: 12px; justify-content: flex-end;">
                        <button type="button" id="feedback-cancel" style="padding: 8px 16px; border: 1px solid #D1D5DB; background: white; border-radius: 6px; cursor: pointer;">Cancel</button>
                        <button type="submit" style="padding: 8px 16px; background: #3B82F6; color: white; border: none; border-radius: 6px; cursor: pointer;">Send Feedback</button>
                    </div>
                </form>
                
                <div id="feedback-success" style="display: none; text-align: center; color: #059669;">
                    <p>✅ Thank you for your feedback!</p>
                </div>
            </div>
        `;

        document.body.appendChild(button);
        document.body.appendChild(modal);
    }

    attachEventListeners() {
        const button = document.getElementById('feedback-btn');
        const modal = document.getElementById('feedback-modal');
        const closeBtn = document.getElementById('feedback-close');
        const cancelBtn = document.getElementById('feedback-cancel');
        const form = document.getElementById('feedback-form');
        const textarea = document.getElementById('feedback-message');
        const charCount = document.getElementById('char-count');
        const stars = document.querySelectorAll('.star');

        // Show modal
        button.addEventListener('click', () => {
            modal.style.display = 'flex';
            this.isVisible = true;
        });

        // Hide modal
        const hideModal = () => {
            modal.style.display = 'none';
            this.isVisible = false;
            this.resetForm();
        };

        closeBtn.addEventListener('click', hideModal);
        cancelBtn.addEventListener('click', hideModal);
        
        // Click outside to close
        modal.addEventListener('click', (e) => {
            if (e.target === modal) hideModal();
        });

        // Character counter
        textarea.addEventListener('input', () => {
            charCount.textContent = textarea.value.length;
        });

        // Star rating
        let selectedRating = 0;
        stars.forEach(star => {
            star.addEventListener('click', () => {
                selectedRating = parseInt(star.dataset.rating);
                this.updateStars(selectedRating);
            });
            
            star.addEventListener('mouseenter', () => {
                this.updateStars(parseInt(star.dataset.rating), true);
            });
        });

        document.getElementById('rating-stars').addEventListener('mouseleave', () => {
            this.updateStars(selectedRating);
        });

        // Form submission
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const formData = {
                type: document.getElementById('feedback-type').value,
                message: document.getElementById('feedback-message').value.trim(),
                rating: selectedRating || null,
                page: window.location.pathname,
                timestamp: new Date().toISOString()
            };

            try {
                const response = await fetch('/feedback', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Session-ID': this.sessionId
                    },
                    body: JSON.stringify(formData)
                });

                if (response.ok) {
                    document.getElementById('feedback-form').style.display = 'none';
                    document.getElementById('feedback-success').style.display = 'block';
                    
                    setTimeout(() => {
                        hideModal();
                    }, 2000);
                } else {
                    throw new Error('Failed to submit feedback');
                }
            } catch (error) {
                console.error('Feedback submission error:', error);
                alert('Failed to submit feedback. Please try again.');
            }
        });
    }

    updateStars(rating, isHover = false) {
        const stars = document.querySelectorAll('.star');
        stars.forEach((star, index) => {
            const starRating = index + 1;
            if (starRating <= rating) {
                star.style.color = isHover ? '#FBBF24' : '#F59E0B';
            } else {
                star.style.color = '#D1D5DB';
            }
        });
    }

    resetForm() {
        document.getElementById('feedback-form').style.display = 'block';
        document.getElementById('feedback-success').style.display = 'none';
        document.getElementById('feedback-form').reset();
        document.getElementById('char-count').textContent = '0';
        this.updateStars(0);
    }
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        new FeedbackWidget();
    });
} else {
    new FeedbackWidget();
}
