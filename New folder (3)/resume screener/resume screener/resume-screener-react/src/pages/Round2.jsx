import React, { useEffect, useMemo, useState, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { submitRound2Answer } from '../api';
import { Card, Button } from '../components/Shared';
import { Terminal, Maximize2, ShieldAlert, Lock, Eye } from 'lucide-react';

const TOTAL_QUESTIONS = 20;

function useQuery() {
    return new URLSearchParams(useLocation().search);
}

const getRound2ErrorMessage = (message) => {
    const text = String(message || '').toLowerCase();
    if (text.includes('quiz session not found') || text.includes('session not found') || text.includes('expired') || text.includes('invalid')) {
        return 'This Round 2 session has expired or is invalid. Please return to the application and start Round 2 again.';
    }
    if (text.includes('failed to fetch') || text.includes('network') || text.includes('service unavailable')) {
        return 'The Round 2 service is currently unavailable. Please try again in a moment.';
    }
    return 'Unable to continue Round 2 right now. Please try again.';
};

export default function Round2() {
    const query = useQuery();
    const navigate = useNavigate();
    const sessionId = query.get('session_id');

    const [questions, setQuestions] = useState([]);
    const [questionIndex, setQuestionIndex] = useState(0);
    const [question, setQuestion] = useState(null);
    const [selected, setSelected] = useState('');
    const [loading, setLoading] = useState(false);
    const [loadingInitial, setLoadingInitial] = useState(false);
    const [finishedReport, setFinishedReport] = useState(null);
    const [error, setError] = useState('');
    const [timerSeconds, setTimerSeconds] = useState(30 * 60);
    const [submitted, setSubmitted] = useState(false);

    // Fullscreen & anti-cheat proctoring states
    const [assessmentStarted, setAssessmentStarted] = useState(false);
    const [isFullscreen, setIsFullscreen] = useState(Boolean(document.fullscreenElement));
    const [fullscreenSupported, setFullscreenSupported] = useState(Boolean(document.documentElement.requestFullscreen));
    const [tabSwitches, setTabSwitches] = useState(0);
    const [fullscreenExits, setFullscreenExits] = useState(0);
    const [proctorWarning, setProctorWarning] = useState('');

    const enterFullscreen = useCallback(async () => {
        try {
            if (!document.fullscreenElement && document.documentElement.requestFullscreen) {
                await document.documentElement.requestFullscreen();
            }
            setIsFullscreen(true);
            setAssessmentStarted(true);
            setProctorWarning('');
        } catch (err) {
            // If browser blocks fullscreen (e.g., restricted iframe), still allow starting with warning
            setFullscreenSupported(false);
            setAssessmentStarted(true);
            setProctorWarning('Fullscreen was restricted by your browser; tab-switch monitoring remains active.');
        }
    }, []);

    useEffect(() => {
        if (!sessionId) return;

        const storedKey = `round2_initial_${sessionId}`;
        try {
            sessionStorage.removeItem(storedKey);
        } catch (e) {
            // ignore storage errors
        }

        const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
        const fetchInitial = async () => {
            if (loadingInitial) return;
            setLoadingInitial(true);
            try {
                const res = await fetch(`${API_URL}/round2/question?session_id=${encodeURIComponent(sessionId)}`);
                if (!res.ok) {
                    let detail = '';
                    try {
                        const payload = await res.json();
                        detail = payload?.detail || JSON.stringify(payload);
                    } catch (e) {
                        detail = await res.text();
                    }
                    if (res.status === 404) {
                        setError(getRound2ErrorMessage(detail || 'Session not found'));
                    } else {
                        setError('The Round 2 service is currently unavailable. Please try again in a moment.');
                    }
                    return;
                }
                const data = await res.json();
                const q = data.next_question || null;
                if (q) {
                    setQuestion(q);
                    setQuestions([q]);
                } else if (data.report) {
                    setFinishedReport(data.report);
                    setAssessmentStarted(true);
                } else {
                    setError('No initial question found for this session.');
                }
            } catch (err) {
                setError('Network error fetching initial question: ' + (err.message || err) + '. Check that backend services are running and VITE_API_URL is correct.');
            } finally {
                setLoadingInitial(false);
            }
        };
        fetchInitial();
    }, [sessionId]);

    // Monitor fullscreen state & tab/window switching
    useEffect(() => {
        if (!sessionId || finishedReport) return;

        const handleFullscreenChange = () => {
            const active = Boolean(document.fullscreenElement);
            setIsFullscreen(active);
            if (!active && assessmentStarted && !finishedReport) {
                setFullscreenExits((prev) => prev + 1);
                setProctorWarning('Fullscreen exited! Re-enter fullscreen immediately to continue your assessment.');
            }
        };

        const handleVisibilityChange = () => {
            if (document.hidden && assessmentStarted && !finishedReport) {
                setTabSwitches((prev) => prev + 1);
                setProctorWarning('Tab switch detected! External tools and tab switching are monitored and logged.');
            }
        };

        const handleWindowBlur = () => {
            if (assessmentStarted && !finishedReport) {
                setTabSwitches((prev) => prev + 1);
                setProctorWarning('Focus left the assessment window. Stay inside the proctored test window.');
            }
        };

        document.addEventListener('fullscreenchange', handleFullscreenChange);
        document.addEventListener('visibilitychange', handleVisibilityChange);
        window.addEventListener('blur', handleWindowBlur);

        return () => {
            document.removeEventListener('fullscreenchange', handleFullscreenChange);
            document.removeEventListener('visibilitychange', handleVisibilityChange);
            window.removeEventListener('blur', handleWindowBlur);
        };
    }, [sessionId, assessmentStarted, finishedReport]);

    // Exit fullscreen automatically once assessment finishes
    useEffect(() => {
        if (finishedReport && document.fullscreenElement && document.exitFullscreen) {
            document.exitFullscreen().catch(() => {});
        }
    }, [finishedReport]);

    useEffect(() => {
        if (finishedReport || !sessionId || !assessmentStarted) return;
        const timer = setInterval(() => {
            setTimerSeconds((prev) => {
                if (prev <= 1) {
                    clearInterval(timer);
                    if (!loading && !submitted) {
                        window.alert('Time is up. Your current answer will be submitted automatically.');
                        handleAutoSubmit();
                    }
                    return 0;
                }
                return prev - 1;
            });
        }, 1000);
        return () => clearInterval(timer);
    }, [finishedReport, loading, sessionId, submitted, assessmentStarted]);

    const progress = useMemo(() => {
        if (!questions.length) return 0;
        return ((questionIndex + 1) / TOTAL_QUESTIONS) * 100;
    }, [questionIndex, questions.length]);

    const persistAnswer = (currentQuestion, value) => {
        if (!sessionId) return;
        const key = `round2_answers_${sessionId}`;
        try {
            const current = JSON.parse(sessionStorage.getItem(key) || '{}');
            current[currentQuestion?.id || 'current'] = value;
            sessionStorage.setItem(key, JSON.stringify(current));
        } catch (e) {
            // ignore storage errors
        }
    };

    const handleAutoSubmit = async () => {
        if (!question || loading || submitted) return;
        const answerValue = selected || '(Candidate Skipped)';
        persistAnswer(question, answerValue);
        setSubmitted(true);
        setLoading(true);
        try {
            const res = await submitRound2Answer({ session_id: sessionId, answer: answerValue });
            if (res.next_question) {
                setQuestion(res.next_question);
                setSelected(sessionStorage.getItem(`round2_answers_${sessionId}`) ? JSON.parse(sessionStorage.getItem(`round2_answers_${sessionId}`))[res.next_question.id] || '' : '');
                setQuestions((prev) => [...prev, res.next_question]);
                setQuestionIndex((prev) => prev + 1);
            } else if (res.report) {
                setFinishedReport(res.report);
            }
        } catch (err) {
            const msg = err?.message || err;
            setError(getRound2ErrorMessage(msg));
        } finally {
            setLoading(false);
        }
    };

    const handleSubmit = async () => {
        if (!question || loading || submitted) return;
        const answerValue = selected || '(Candidate Skipped)';
        persistAnswer(question, answerValue);
        setSubmitted(true);
        setLoading(true);
        setError('');
        try {
            const res = await submitRound2Answer({ session_id: sessionId, answer: answerValue });
            if (res.next_question) {
                setQuestion(res.next_question);
                setSelected('');
                setQuestionIndex((prev) => prev + 1);
                setQuestions((prev) => [...prev, res.next_question]);
            } else if (res.report) {
                setFinishedReport(res.report);
            }
        } catch (err) {
            const msg = err?.message || err;
            setError(getRound2ErrorMessage(msg));
        } finally {
            setLoading(false);
            setSubmitted(false);
        }
    };

    const handleSkip = async () => {
        if (!question || loading) return;
        const answerValue = '(Candidate Skipped)';
        persistAnswer(question, answerValue);
        setSubmitted(true);
        setLoading(true);
        setError('');
        try {
            const res = await submitRound2Answer({ session_id: sessionId, answer: answerValue });
            if (res.next_question) {
                setQuestion(res.next_question);
                setSelected('');
                setQuestionIndex((prev) => prev + 1);
                setQuestions((prev) => [...prev, res.next_question]);
            } else if (res.report) {
                setFinishedReport(res.report);
            }
        } catch (err) {
            const msg = err?.message || err;
            setError(getRound2ErrorMessage(msg));
        } finally {
            setLoading(false);
            setSubmitted(false);
        }
    };

    if (!sessionId) {
        return (
            <div style={{ maxWidth: '640px', margin: '2rem auto', padding: '1rem' }}>
                <Card className="card-glow-accent fade-in" style={{ textAlign: 'center', padding: '2.5rem 1.5rem' }}>
                    <div style={{ width: '56px', height: '56px', borderRadius: '50%', background: 'rgba(99, 102, 241, 0.15)', color: '#818cf8', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 1rem' }}>
                        <Terminal size={28} />
                    </div>
                    <h2 style={{ marginBottom: '8px' }}>Round 2 Technical Evaluation</h2>
                    <p style={{ color: '#94a3b8', maxWidth: '440px', margin: '0 auto 1.5rem', fontSize: '0.92rem' }}>
                        To participate in the Round 2 Technical Assessment, please complete the Round 1 Resume Screening first.
                    </p>
                    <Button variant="primary" onClick={() => navigate('/')}>
                        ← Go to Candidate Portal
                    </Button>
                </Card>
            </div>
        );
    }

    const minutes = String(Math.floor(timerSeconds / 60)).padStart(2, '0');
    const seconds = String(timerSeconds % 60).padStart(2, '0');
    const mustLockForFullscreen = assessmentStarted && !finishedReport && fullscreenSupported && !isFullscreen;

    return (
        <div
            className="fade-in"
            style={{
                maxWidth: isFullscreen ? '1180px' : '960px',
                width: '100%',
                margin: '0 auto',
                paddingBottom: '3rem'
            }}
        >
            {/* Proctored Fullscreen Launch Gate */}
            {!assessmentStarted && !finishedReport && !error && (
                <Card className="card-glow-accent" style={{ textAlign: 'center', padding: '3rem 2rem', marginBottom: '2rem' }}>
                    <div style={{ width: '64px', height: '64px', borderRadius: '50%', background: 'rgba(99, 102, 241, 0.16)', color: '#818cf8', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 1.25rem' }}>
                        <Maximize2 size={30} />
                    </div>
                    <h2 style={{ fontSize: '1.75rem', marginBottom: '10px' }}>Fullscreen Proctored Assessment (20 Questions)</h2>
                    <p style={{ color: '#94a3b8', maxWidth: '580px', margin: '0 auto 1.5rem', lineHeight: 1.6, fontSize: '0.95rem' }}>
                        To ensure fair evaluation without external AI assistance, Round 2 runs in locked <strong>Fullscreen Mode</strong>.
                        Tab switching, exiting fullscreen, and copying question text are monitored and logged.
                    </p>
                    <div style={{ display: 'flex', justifyContent: 'center', gap: '1rem', flexWrap: 'wrap', marginBottom: '1.75rem', fontSize: '0.85rem', color: '#cbd5e1' }}>
                        <span>• 20 Technical Questions (200 pts)</span>
                        <span>• 30-Minute Timer</span>
                        <span>• ≥ 70% Required for Round 3</span>
                    </div>
                    <Button variant="primary" onClick={enterFullscreen} style={{ padding: '0.95rem 2.2rem', fontSize: '1.05rem' }}>
                        <Maximize2 size={18} />
                        <span>Enter Fullscreen & Begin Round 2</span>
                    </Button>
                </Card>
            )}

            {/* Fullscreen Re-entry Lock Overlay */}
            {mustLockForFullscreen && (
                <Card style={{ textAlign: 'center', padding: '3rem 2rem', border: '2px solid rgba(239, 68, 68, 0.5)', background: 'rgba(127, 29, 29, 0.25)', marginBottom: '1.5rem' }}>
                    <Lock size={38} color="#f87171" style={{ margin: '0 auto 12px' }} />
                    <h2 style={{ color: '#fca5a5', marginBottom: '8px' }}>Assessment Paused — Fullscreen Required</h2>
                    <p style={{ color: '#e2e8f0', maxWidth: '520px', margin: '0 auto 1.5rem', fontSize: '0.94rem' }}>
                        You exited fullscreen mode ({fullscreenExits} exit{fullscreenExits === 1 ? '' : 's'} logged). Return to fullscreen to reveal the active question.
                    </p>
                    <Button variant="primary" onClick={enterFullscreen} style={{ background: 'linear-gradient(135deg, #ef4444, #dc2626)' }}>
                        <Maximize2 size={16} />
                        <span>Re-enter Fullscreen to Continue</span>
                    </Button>
                </Card>
            )}

            {(assessmentStarted || finishedReport || error) && !mustLockForFullscreen && (
                <Card className="card-glow-accent">
                    {/* Header */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'center', flexWrap: 'wrap', marginBottom: '1.25rem' }}>
                        <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px', flexWrap: 'wrap' }}>
                                <span className="badge-pill badge-active">Round 2: Technical (20Q)</span>
                                <span style={{ fontSize: '0.8rem', color: '#94a3b8' }}>Session: #{sessionId.slice(0, 8)}</span>
                                {!finishedReport && (
                                    <span style={{ fontSize: '0.75rem', padding: '3px 10px', borderRadius: '999px', background: isFullscreen ? 'rgba(16, 185, 129, 0.14)' : 'rgba(245, 158, 11, 0.16)', color: isFullscreen ? '#34d399' : '#fbbf24', display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
                                        <Eye size={12} />
                                        {isFullscreen ? 'Fullscreen Proctored' : 'Monitored Mode'}
                                        {(tabSwitches + fullscreenExits) > 0 ? ` • ${tabSwitches + fullscreenExits} warning(s)` : ''}
                                    </span>
                                )}
                            </div>
                            <h2 style={{ margin: 0, fontSize: '1.65rem' }}>Technical Proficiency Assessment</h2>
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                            {!finishedReport && fullscreenSupported && !isFullscreen && (
                                <Button variant="outline" className="btn-sm" onClick={enterFullscreen}>
                                    <Maximize2 size={14} /> Fullscreen
                                </Button>
                            )}
                            <div style={{ background: 'rgba(15, 23, 42, 0.9)', border: '1px solid rgba(255, 255, 255, 0.1)', borderRadius: '12px', padding: '0.5rem 1rem', minWidth: '130px', textAlign: 'center' }}>
                                <div style={{ fontSize: '0.72rem', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Time Remaining</div>
                                <strong style={{ fontSize: '1.2rem', color: timerSeconds < 300 ? '#f87171' : '#38bdf8', fontFamily: 'monospace' }}>
                                    {minutes}:{seconds}
                                </strong>
                            </div>
                        </div>
                    </div>

                    {proctorWarning && !finishedReport && (
                        <div style={{ marginBottom: '1rem', padding: '0.75rem 1rem', borderRadius: '10px', background: 'rgba(245, 158, 11, 0.14)', border: '1px solid rgba(245, 158, 11, 0.4)', color: '#fcd34d', display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.88rem' }}>
                            <ShieldAlert size={18} />
                            <span>{proctorWarning}</span>
                        </div>
                    )}

                    {!finishedReport && question && (
                        <>
                            {/* Progress Bar */}
                            <div style={{ marginBottom: '1.5rem' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px', color: '#94a3b8', fontSize: '0.85rem' }}>
                                    <span>Question {questionIndex + 1} of {TOTAL_QUESTIONS}</span>
                                    <span>{Math.round(Math.min(100, progress))}% Completed</span>
                                </div>
                                <div style={{ height: '8px', background: 'rgba(255, 255, 255, 0.08)', borderRadius: '999px', overflow: 'hidden' }}>
                                    <div style={{ width: `${Math.max(5, progress)}%`, height: '100%', background: 'linear-gradient(90deg, #6366f1, #38bdf8)', borderRadius: '999px', transition: 'width 0.3s ease' }} />
                                </div>
                            </div>

                            {/* Question Box (copy/context-menu protected) */}
                            <div
                                onCopy={(e) => e.preventDefault()}
                                onCut={(e) => e.preventDefault()}
                                onContextMenu={(e) => e.preventDefault()}
                                style={{ userSelect: 'none', marginBottom: '1.25rem', padding: '1.35rem 1.5rem', borderRadius: '14px', background: 'rgba(11, 17, 33, 0.85)', border: '1px solid rgba(99, 102, 241, 0.2)' }}
                            >
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                                    <span style={{ color: '#818cf8', fontSize: '0.78rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                                        {question.type ? question.type.replace('_', ' ').toUpperCase() : 'TECHNICAL QUESTION'}
                                    </span>
                                    <span style={{ fontSize: '0.75rem', color: '#64748b' }}>10 Points</span>
                                </div>
                                <h3 style={{ margin: 0, lineHeight: 1.55, fontSize: '1.18rem', color: '#f8fafc', whiteSpace: 'pre-wrap' }}>
                                    {question.question}
                                </h3>
                            </div>

                            {/* MCQ Options */}
                            {question.type === 'mcq' && question.options && question.options.length > 0 && (
                                <div style={{ display: 'grid', gap: '0.75rem' }}>
                                    {question.options.map((opt, idx) => (
                                        <label
                                            key={idx}
                                            style={{
                                                display: 'flex',
                                                alignItems: 'center',
                                                gap: '0.85rem',
                                                padding: '1rem 1.15rem',
                                                borderRadius: '12px',
                                                border: selected === opt ? '1.5px solid #6366f1' : '1px solid rgba(255, 255, 255, 0.08)',
                                                background: selected === opt ? 'rgba(99, 102, 241, 0.16)' : 'rgba(15, 23, 42, 0.6)',
                                                cursor: 'pointer',
                                                transition: 'all 0.2s ease'
                                            }}
                                        >
                                            <input
                                                type="radio"
                                                name="opt"
                                                value={opt}
                                                checked={selected === opt}
                                                onChange={() => setSelected(opt)}
                                                style={{ accentColor: '#6366f1' }}
                                            />
                                            <span style={{ fontSize: '0.94rem', color: selected === opt ? '#ffffff' : '#cbd5e1' }}>{opt}</span>
                                        </label>
                                    ))}
                                </div>
                            )}

                            {/* Open ended text response */}
                            {question.type !== 'mcq' && (
                                <div>
                                    <textarea
                                        value={selected}
                                        onChange={(e) => setSelected(e.target.value)}
                                        rows={7}
                                        style={{ width: '100%', background: 'rgba(11, 17, 33, 0.9)', color: '#f8fafc', border: '1px solid rgba(99, 102, 241, 0.25)', borderRadius: '12px', padding: '1rem', fontFamily: 'monospace', fontSize: '0.92rem', lineHeight: 1.6 }}
                                        placeholder="Type your technical solution or code implementation here..."
                                    />
                                </div>
                            )}

                            {error && (
                                <div style={{ marginTop: '1rem', color: '#fca5a5', background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)', padding: '0.8rem 1rem', borderRadius: '10px', fontSize: '0.88rem' }}>
                                    {error}
                                </div>
                            )}

                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', marginTop: '1.75rem', flexWrap: 'wrap' }}>
                                <div style={{ display: 'flex', gap: '0.75rem' }}>
                                    <Button variant="outline" className="btn-sm" onClick={() => setSelected('')} disabled={loading}>
                                        Clear
                                    </Button>
                                    <Button variant="outline" className="btn-sm" onClick={() => handleSkip()} disabled={loading}>
                                        Skip Question
                                    </Button>
                                </div>
                                <Button variant="primary" onClick={handleSubmit} disabled={loading} style={{ minWidth: '170px' }}>
                                    {loading ? 'Submitting...' : (questionIndex + 1 >= TOTAL_QUESTIONS ? 'Submit Final Answer →' : 'Submit & Next →')}
                                </Button>
                            </div>
                        </>
                    )}

                    {!finishedReport && !question && !error && (
                        <div style={{ color: '#cbd5e1', textAlign: 'center', padding: '2rem' }}>
                            Loading next technical challenge...
                        </div>
                    )}

                    {/* FINISHED REPORT */}
                    {finishedReport && (
                        <div className="fade-in" style={{ textAlign: 'center', padding: '1.5rem 0' }}>
                            <div className="score-circle" style={{
                                '--percentage': `${finishedReport.percentage ?? finishedReport.totalScore}%`,
                                color: finishedReport.isSelected ? '#10b981' : '#ef4444',
                                width: '100px',
                                height: '100px',
                                fontSize: '2rem'
                            }}>
                                <span className="score-value">{Math.round(finishedReport.percentage ?? finishedReport.totalScore)}%</span>
                            </div>

                            <h2 style={{ fontSize: '1.75rem', marginBottom: '6px' }}>
                                {finishedReport.isSelected ? '🎉 Qualified for Round 3!' : 'Technical Assessment Completed'}
                            </h2>

                            <div style={{ display: 'flex', justifyContent: 'center', gap: '10px', flexWrap: 'wrap', marginBottom: '1.5rem' }}>
                                <span className={`badge-pill ${finishedReport.isSelected ? 'badge-passed' : 'badge-failed'}`} style={{ fontSize: '0.85rem', padding: '6px 16px' }}>
                                    Status: {finishedReport.isSelected ? 'QUALIFIED (>= 70%)' : 'NOT QUALIFIED (< 70%)'}
                                </span>
                                <span className="badge-pill" style={{ fontSize: '0.82rem', padding: '6px 14px', background: 'rgba(148, 163, 184, 0.14)', color: '#cbd5e1' }}>
                                    Score: {finishedReport.totalScore ?? 0} / {finishedReport.maxScore ?? 200}
                                </span>
                            </div>

                            <div style={{ textAlign: 'left', background: 'rgba(15, 23, 42, 0.85)', padding: '1.25rem', borderRadius: '12px', color: '#cbd5e1', border: '1px solid rgba(255, 255, 255, 0.08)', marginBottom: '1.5rem', lineHeight: 1.6, fontSize: '0.92rem' }}>
                                {finishedReport.overallFeedback || finishedReport.summary || 'Assessment evaluation complete.'}
                            </div>

                            {finishedReport.isSelected && (
                                <div style={{ marginTop: '1.5rem' }}>
                                    <Button
                                        variant="primary"
                                        onClick={() => {
                                            const candidateId = sessionStorage.getItem('current_candidate_id') || sessionStorage.getItem('round2_candidate_id');
                                            if (candidateId) {
                                                sessionStorage.setItem('current_candidate_id', candidateId);
                                                sessionStorage.setItem('round3_candidate_id', candidateId);
                                                navigate(`/round3?candidate_id=${encodeURIComponent(candidateId)}`);
                                            } else {
                                                navigate('/round3');
                                            }
                                        }}
                                        style={{ width: '100%', padding: '1rem', fontSize: '1.1rem', background: 'linear-gradient(135deg, #10b981, #059669)' }}
                                    >
                                        🚀 Proceed to Round 3: AI Voice & Video Interview →
                                    </Button>
                                </div>
                            )}
                        </div>
                    )}
                </Card>
            )}
        </div>
    );
}
