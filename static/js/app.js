document.addEventListener('DOMContentLoaded', () => {
  const timerEl = document.getElementById('timer');
  if (timerEl) {
    let minutes = parseInt(timerEl.dataset.minutes || '60', 10);
    let seconds = 0;
    const tick = () => {
      if (seconds === 0) { if (minutes === 0) { const f = document.querySelector('form[action*="finish"]'); if (f) f.submit(); return; } minutes--; seconds = 59; }
      else { seconds--; }
      const mm = String(minutes).padStart(2,'0');
      const ss = String(seconds).padStart(2,'0');
      timerEl.textContent = `${mm}:${ss}`;
    };
    setInterval(tick, 1000);
  }

  const sendAnswer = (submissionId, questionId, response) => {
    fetch('/api/answer', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({submission_id: submissionId, question_id: questionId, response})
    }).then(r => r.json()).then(_ => {}).catch(_ => {});
  };

  document.querySelectorAll('.qa-input').forEach(el => {
    const submissionId = el.dataset.submissionId;
    const questionId = el.dataset.questionId;
    if (el.type === 'radio') {
      el.addEventListener('change', () => {
        sendAnswer(submissionId, questionId, el.value);
      });
    } else {
      let t;
      el.addEventListener('input', () => {
        clearTimeout(t);
        t = setTimeout(() => sendAnswer(submissionId, questionId, el.value), 500);
      });
    }
  });
});
