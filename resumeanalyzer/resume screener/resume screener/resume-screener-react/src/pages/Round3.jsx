import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { startRound3, submitRound3Result } from '../api';
import { Card, Button } from '../components/Shared';
import {
    Mic,
    MicOff,
    Video,
    VideoOff,
    Sparkles,
    CheckCircle2,
    ArrowRight,
    Award,
    AlertCircle,
    ShieldAlert,
    RotateCcw,
    Send,
    Eye
} from 'lucide-react';

const INTERVIEW_API_BASE = import.meta.env.VITE_INTERVIEW_API_URL || 'http://localhost:8004';

const INTERVIEW_QUESTIONS = [
    {
        id: 1,
        category: "Professional Background & Core Strengths",
        question: "Please introduce yourself, highlight your primary technical competencies, and explain what motivates your engineering approach."
    },
    {
        id: 2,
        category: "Technical Problem Solving & Architecture",
        question: "Describe a complex technical challenge or production issue you resolved. Walk us through your diagnosis, approach, and outcome."
    },
    {
        id: 3,
        category: "Collaboration & Conflict Management",
        question: "Tell us about a time you had a technical disagreement with a team member or stakeholder. How did you navigate it to consensus?"
    },
    {
        id: 4,
        category: "Resilience, Failure & Continuous Learning",
        question: "Describe a project or technical decision that did not go as planned. What was the impact, what did you learn, and how did you adapt?"
    },
    {
        id: 5,
        category: "Future Impact & Role Alignment",
        question: "Where do you envision your technical leadership in the next 2-3 years, and why is this role the ideal catalyst for your growth?"
    }
];

function useQuery() {
    return new URLSearchParams(useLocation().search);
}

export default function Round3() {
    const query = useQuery();
    const navigate = useNavigate();
    const candidateId = query.get('candidate_id') || sessionStorage.getItem('current_candidate_id') || sessionStorage.getItem('round3_candidate_id') || sessionStorage.getItem('round2_candidate_id');

    const [session, setSession] = useState(null);
    const [loadingSession, setLoadingSession] = useState(false);
    const [sessionError, setSessionError] = useState('');

    const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
    const [allAnswers, setAllAnswers] = useState({});
    const [allEvaluations, setAllEvaluations] = useState({});

    const [transcript, setTranscript] = useState('');
    const [isListening, setIsListening] = useState(false);
    const [speechSupported, setSpeechSupported] = useState(true);
    const [recordingDuration, setRecordingDuration] = useState(0);

    const [cameraEnabled, setCameraEnabled] = useState(false);
    const [cameraStream, setCameraStream] = useState(null);

    // Real-time Webcam Proctoring & Visibility Alerts
    const [proctorStatus, setProctorStatus] = useState('INITIALIZING'); // OK | CAMERA_OFF | CAMERA_COVERED | NOT_VISIBLE | TAB_SWITCH
    const [proctorMessage, setProctorMessage] = useState('Initializing camera verification...');
    const [proctorAlerts, setProctorAlerts] = useState([]);

    const [evaluating, setEvaluating] = useState(false);
    const [currentEvaluation, setCurrentEvaluation] = useState(null);
    const [interviewFinished, setInterviewFinished] = useState(false);
    const [finalReport, setFinalReport] = useState(null);

    const recognitionRef = useRef(null);
    const activeRecordingRef = useRef(false);
    const finalTranscriptRef = useRef('');
    const timerRef = useRef(null);
    const videoRef = useRef(null);
    const canvasRef = useRef(null);
    const faceDetectorRef = useRef(null);
    const noFaceStreakRef = useRef(0);
    const prevFrameRef = useRef(null);
    const lastAlertCodeRef = useRef('');

    const logProctorAlert = useCallback((code, message) => {
        setProctorStatus(code);
        setProctorMessage(message);
        if (code !== 'OK' && lastAlertCodeRef.current !== code) {
            lastAlertCodeRef.current = code;
            setProctorAlerts((prev) => [
                ...prev,
                { code, message, timestamp: new Date().toLocaleTimeString() }
            ]);
        } else if (code === 'OK') {
            lastAlertCodeRef.current = 'OK';
        }
    }, []);

    // Initialize Round 3 session
    useEffect(() => {
        if (!candidateId) {
            setSessionError('No candidate session found. Please return to the Candidate Portal or continue from Round 2.');
            return;
        }

        const init = async () => {
            setLoadingSession(true);
            setSessionError('');
            try {
                const data = await startRound3(candidateId);
                setSession(data);
                if (data?.candidate_id) {
                    sessionStorage.setItem('current_candidate_id', data.candidate_id);
                    sessionStorage.setItem('round3_candidate_id', data.candidate_id);
                }
            } catch (err) {
                setSession({
                    candidate_id: candidateId,
                    candidate_name: 'Candidate',
                    domain: 'Engineering',
                    status: 'ready'
                });
            } finally {
                setLoadingSession(false);
            }
        };

        init();
    }, [candidateId]);

    // Check Speech Recognition & FaceDetector support on mount
    useEffect(() => {
        const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRec) {
            setSpeechSupported(false);
        }
        if ('FaceDetector' in window) {
            try {
                faceDetectorRef.current = new window.FaceDetector({ fastMode: true, maxDetectedFaces: 2 });
            } catch (e) {
                faceDetectorRef.current = null;
            }
        }
    }, []);

    const stopCameraTracks = useCallback((streamToStop) => {
        const target = streamToStop || cameraStream;
        if (target) {
            target.getTracks().forEach((track) => track.stop());
        }
    }, [cameraStream]);

    // Cleanup camera and mic on unmount
    useEffect(() => {
        return () => {
            stopListeningClean();
            if (cameraStream) {
                cameraStream.getTracks().forEach((track) => track.stop());
            }
            if (timerRef.current) {
                clearInterval(timerRef.current);
            }
        };
    }, [cameraStream]);

    // Automatically enable camera stream on mount
    useEffect(() => {
        let mounted = true;
        const autoStartCamera = async () => {
            try {
                const stream = await navigator.mediaDevices.getUserMedia({
                    video: { width: 640, height: 480 },
                    audio: false
                });
                if (mounted) {
                    const videoTrack = stream.getVideoTracks()[0];
                    if (videoTrack) {
                        videoTrack.onended = () => {
                            setCameraEnabled(false);
                            logProctorAlert('CAMERA_OFF', 'Camera disconnected or turned off! Re-enable your webcam immediately.');
                        };
                        videoTrack.onmute = () => {
                            logProctorAlert('CAMERA_OFF', 'Camera feed muted or hidden! Check your webcam shutter/permissions.');
                        };
                    }
                    setCameraStream(stream);
                    setCameraEnabled(true);
                    logProctorAlert('OK', 'Webcam active • Candidate visible');
                    if (videoRef.current) {
                        videoRef.current.srcObject = stream;
                    }
                } else {
                    stream.getTracks().forEach((t) => t.stop());
                }
            } catch (err) {
                if (mounted) {
                    setCameraEnabled(false);
                    logProctorAlert('CAMERA_OFF', 'Webcam access blocked or unavailable! Enable camera permissions for live interview proctoring.');
                }
            }
        };
        autoStartCamera();
        return () => {
            mounted = false;
        };
    }, [logProctorAlert]);

    const toggleCamera = async () => {
        if (cameraEnabled) {
            stopCameraTracks();
            setCameraStream(null);
            setCameraEnabled(false);
            logProctorAlert('CAMERA_OFF', 'Webcam turned off! Please re-enable your camera so the proctor can verify your presence.');
        } else {
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false });
                const videoTrack = stream.getVideoTracks()[0];
                if (videoTrack) {
                    videoTrack.onended = () => {
                        setCameraEnabled(false);
                        logProctorAlert('CAMERA_OFF', 'Camera disconnected! Re-enable your webcam.');
                    };
                }
                setCameraStream(stream);
                setCameraEnabled(true);
                noFaceStreakRef.current = 0;
                logProctorAlert('OK', 'Webcam active • Candidate visible');
                if (videoRef.current) {
                    videoRef.current.srcObject = stream;
                }
            } catch (err) {
                logProctorAlert('CAMERA_OFF', 'Could not access camera: ' + err.message);
            }
        }
    };

    useEffect(() => {
        if (cameraEnabled && cameraStream && videoRef.current) {
            videoRef.current.srcObject = cameraStream;
        }
    }, [cameraEnabled, cameraStream]);

    // Real-time frame analysis loop: detects covered/blocked camera & user absence
    useEffect(() => {
        if (interviewFinished) return;

        if (!cameraEnabled || !cameraStream) {
            logProctorAlert('CAMERA_OFF', 'Camera is turned off or hidden! Enable your webcam to avoid interview flagging.');
            return;
        }

        const interval = setInterval(async () => {
            const video = videoRef.current;
            if (!video || video.readyState < 2 || video.videoWidth === 0) return;

            if (!canvasRef.current) {
                canvasRef.current = document.createElement('canvas');
            }
            const canvas = canvasRef.current;
            const w = 96;
            const h = 72;
            canvas.width = w;
            canvas.height = h;
            const ctx = canvas.getContext('2d', { willReadFrequently: true });
            if (!ctx) return;

            ctx.drawImage(video, 0, 0, w, h);
            const imgData = ctx.getImageData(0, 0, w, h).data;

            // 1. Compute frame luminance mean & variance (detect covered/blacked-out/uniform camera)
            let sumLuma = 0;
            let sumSqLuma = 0;
            let skinLikePixels = 0;
            let centerDiff = 0;
            const pixelCount = w * h;
            const currGray = new Uint8Array(pixelCount);

            for (let y = 0; y < h; y++) {
                for (let x = 0; x < w; x++) {
                    const pIdx = y * w + x;
                    const i = pIdx * 4;
                    const r = imgData[i];
                    const g = imgData[i + 1];
                    const b = imgData[i + 2];
                    const luma = 0.299 * r + 0.587 * g + 0.114 * b;
                    currGray[pIdx] = luma;
                    sumLuma += luma;
                    sumSqLuma += luma * luma;

                    // Center ROI heuristic for human presence when FaceDetector isn't available
                    if (x >= 20 && x <= 76 && y >= 10 && y <= 62) {
                        if (r > 60 && g > 40 && b > 20 && r > g && r > b && Math.abs(r - g) > 10) {
                            skinLikePixels++;
                        }
                        if (prevFrameRef.current) {
                            centerDiff += Math.abs(luma - prevFrameRef.current[pIdx]);
                        }
                    }
                }
            }

            const meanLuma = sumLuma / pixelCount;
            const variance = Math.max(0, (sumSqLuma / pixelCount) - (meanLuma * meanLuma));
            const stdDev = Math.sqrt(variance);
            const avgMotion = prevFrameRef.current ? centerDiff / (56 * 52) : 5;
            prevFrameRef.current = currGray;

            // Covered lens / shutter closed / pitch-dark frame
            if (meanLuma < 18 || stdDev < 7.5) {
                noFaceStreakRef.current += 1;
                logProctorAlert('CAMERA_COVERED', 'Alert: Webcam appears covered or blocked! Please uncover your camera immediately.');
                return;
            }

            // 2. Check face / candidate visibility
            let candidateVisible = true;
            if (faceDetectorRef.current) {
                try {
                    const faces = await faceDetectorRef.current.detect(video);
                    candidateVisible = Array.isArray(faces) && faces.length > 0;
                } catch (e) {
                    // Fallback to skin/motion presence check
                    candidateVisible = skinLikePixels > 45 || avgMotion > 2.2;
                }
            } else {
                // Heuristic fallback: requires meaningful skin-tone presence in center ROI or live subject motion
                candidateVisible = skinLikePixels > 45 || avgMotion > 2.5;
            }

            if (!candidateVisible) {
                noFaceStreakRef.current += 1;
                if (noFaceStreakRef.current >= 2) {
                    logProctorAlert('NOT_VISIBLE', 'Alert: Candidate is not clearly visible in camera frame! Please sit centered in front of the webcam.');
                }
            } else {
                noFaceStreakRef.current = 0;
                logProctorAlert('OK', 'Camera Active • Candidate Visible');
            }
        }, 1200);

        return () => clearInterval(interval);
    }, [cameraEnabled, cameraStream, interviewFinished, logProctorAlert]);

    // Tab-switch / window blur alert in Round 3
    useEffect(() => {
        if (interviewFinished) return;
        const onVisChange = () => {
            if (document.hidden) {
                logProctorAlert('TAB_SWITCH', 'Alert: Tab switch detected during live interview! Stay on this tab.');
            }
        };
        document.addEventListener('visibilitychange', onVisChange);
        return () => document.removeEventListener('visibilitychange', onVisChange);
    }, [interviewFinished, logProctorAlert]);

    // --- SPEECH RECOGNITION ---
    const startListening = () => {
        const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRec) {
            alert('Speech Recognition is not supported in this browser. Please use Google Chrome, Microsoft Edge, or type your response.');
            return;
        }

        stopListeningClean();

        try {
            const recognition = new SpeechRec();
            recognition.continuous = true;
            recognition.interimResults = true;
            recognition.lang = 'en-US';

            activeRecordingRef.current = true;
            setIsListening(true);
            setRecordingDuration(0);

            if (timerRef.current) clearInterval(timerRef.current);
            timerRef.current = setInterval(() => {
                setRecordingDuration((prev) => prev + 1);
            }, 1000);

            recognition.onresult = (event) => {
                let interim = '';
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    const res = event.results[i];
                    const text = res[0].transcript;
                    if (res.isFinal) {
                        finalTranscriptRef.current += (finalTranscriptRef.current ? ' ' : '') + text.trim();
                    } else {
                        interim += text;
                    }
                }
                const fullText = (finalTranscriptRef.current + (interim ? ' ' + interim : '')).trim();
                setTranscript(fullText);
            };

            recognition.onerror = (event) => {
                console.warn('Speech recognition status:', event.error);
                if (event.error === 'not-allowed') {
                    activeRecordingRef.current = false;
                    setIsListening(false);
                    alert('Microphone access blocked. Please allow microphone permissions in your browser.');
                }
            };

            recognition.onend = () => {
                if (activeRecordingRef.current) {
                    try {
                        recognition.start();
                    } catch (e) {
                        // ignore
                    }
                } else {
                    setIsListening(false);
                }
            };

            recognitionRef.current = recognition;
            recognition.start();
        } catch (err) {
            console.error('Error launching speech recognition:', err);
            setIsListening(false);
            activeRecordingRef.current = false;
        }
    };

    const stopListening = () => {
        activeRecordingRef.current = false;
        if (timerRef.current) {
            clearInterval(timerRef.current);
            timerRef.current = null;
        }
        if (recognitionRef.current) {
            try {
                recognitionRef.current.stop();
            } catch (e) {
                // ignore
            }
        }
        setIsListening(false);
    };

    const stopListeningClean = () => {
        activeRecordingRef.current = false;
        if (timerRef.current) {
            clearInterval(timerRef.current);
            timerRef.current = null;
        }
        if (recognitionRef.current) {
            try {
                recognitionRef.current.onresult = null;
                recognitionRef.current.onerror = null;
                recognitionRef.current.onend = null;
                recognitionRef.current.abort();
            } catch (e) {
                // ignore
            }
            recognitionRef.current = null;
        }
        setIsListening(false);
    };

    const handleClearTranscript = () => {
        stopListeningClean();
        finalTranscriptRef.current = '';
        setTranscript('');
        setRecordingDuration(0);
    };

    // Genuine local fallback only if port 8004 is unreachable
    const computeHonestOfflineFallback = (answerText) => {
        const words = answerText.trim().split(/\s+/).filter(Boolean).length;
        const hasSTAR = /situation|task|action|result|because|improved|solved|team|built|led|designed|implemented|optimized|architecture|debugged|scaled/i.test(answerText);
        if (words < 8) {
            return {
                confidence: { score: 3.5, justification: `Very brief response (${words} words).`, key_phrases: [] },
                evidence: { score: 2.5, justification: 'Lacks concrete STAR examples or technical detail.', key_phrases: [] },
                clarity: { score: 3.5, justification: 'Needs deeper explanation.', key_phrases: [] },
                arrogance: { score: 0.5, justification: 'Neutral.', key_phrases: [] },
                overall_score: 32,
                feedback: 'Your answer is too short. Use the STAR method with concrete actions and measurable outcomes.',
                model_used: 'offline-fallback'
            };
        }
        const conf = Math.min(9.0, 5.2 + Math.min(words / 20, 2.8) + (hasSTAR ? 0.6 : 0));
        const evid = Math.min(9.0, 4.5 + (hasSTAR ? 2.5 : 0.8) + Math.min(words / 30, 1.5));
        const clar = Math.min(9.0, 5.5 + Math.min(words / 22, 2.5));
        const overall = Math.round(((conf + evid + clar) / 3) * 10);
        return {
            confidence: { score: Number(conf.toFixed(1)), justification: `Delivered ${words} words.`, key_phrases: [] },
            evidence: { score: Number(evid.toFixed(1)), justification: hasSTAR ? 'Included situational/action indicators.' : 'Add more specific STAR metrics.', key_phrases: [] },
            clarity: { score: Number(clar.toFixed(1)), justification: 'Coherent response.', key_phrases: [] },
            arrogance: { score: 0.6, justification: 'Collaborative tone.', key_phrases: [] },
            overall_score: overall,
            feedback: overall >= 80 ? 'Strong structured response.' : 'Add measurable outcomes and deeper technical reasoning to strengthen your score.',
            model_used: 'offline-fallback'
        };
    };

    // Evaluate answer with Gemini Flash via Round 3 backend
    const evaluateAnswer = async () => {
        if (!transcript.trim()) {
            alert('Please record or type your answer before submitting.');
            return;
        }

        stopListening();
        setEvaluating(true);

        const currentQ = INTERVIEW_QUESTIONS[currentQuestionIndex];
        const answerText = transcript.trim();

        const updatedAnswers = { ...allAnswers, [currentQuestionIndex]: answerText };
        setAllAnswers(updatedAnswers);

        try {
            let evalResult = null;
            try {
                const res = await fetch(`${INTERVIEW_API_BASE}/api/evaluate-answer`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        question: currentQ.question,
                        answer: answerText,
                        question_number: currentQuestionIndex + 1,
                        resume_summary: session?.domain || ''
                    })
                });
                if (res.ok) {
                    evalResult = await res.json();
                }
            } catch (netErr) {
                console.warn('Interview API unreachable, using honest offline evaluator:', netErr);
            }

            if (!evalResult || !evalResult.confidence || evalResult.overall_score === undefined) {
                evalResult = computeHonestOfflineFallback(answerText);
            }

            const updatedEvals = { ...allEvaluations, [currentQuestionIndex]: evalResult };
            setAllEvaluations(updatedEvals);
            setCurrentEvaluation(evalResult);

            // Auto-shutdown webcam immediately after submitting the final question
            if (currentQuestionIndex === INTERVIEW_QUESTIONS.length - 1) {
                if (cameraStream) {
                    cameraStream.getTracks().forEach((track) => track.stop());
                    setCameraStream(null);
                }
                setCameraEnabled(false);
            }
        } catch (err) {
            alert('Evaluation error: ' + (err.message || err));
        } finally {
            setEvaluating(false);
        }
    };

    const handleNextQuestion = async () => {
        stopListeningClean();
        setCurrentEvaluation(null);
        setTranscript('');
        finalTranscriptRef.current = '';
        setRecordingDuration(0);

        if (currentQuestionIndex < INTERVIEW_QUESTIONS.length - 1) {
            setCurrentQuestionIndex((prev) => prev + 1);
        } else {
            if (cameraStream) {
                cameraStream.getTracks().forEach((track) => track.stop());
                setCameraStream(null);
            }
            setCameraEnabled(false);
            await generateFinalInterviewReport();
        }
    };

    const generateFinalInterviewReport = async () => {
        setEvaluating(true);

        if (cameraStream) {
            cameraStream.getTracks().forEach((track) => track.stop());
            setCameraStream(null);
        }
        setCameraEnabled(false);

        const qaPayload = INTERVIEW_QUESTIONS.map((q, idx) => ({
            question: q.question,
            category: q.category,
            answer: allAnswers[idx] || '',
            evaluation: allEvaluations[idx] || {}
        }));

        let finalSummary = null;
        try {
            const res = await fetch(`${INTERVIEW_API_BASE}/api/evaluate-final`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    domain: session?.domain || 'Engineering',
                    questions_evaluated: qaPayload,
                    proctoring_alerts: proctorAlerts.length
                })
            });
            if (res.ok) {
                finalSummary = await res.json();
            }
        } catch (err) {
            console.warn('Final AI summary fallback used:', err);
        }

        const evals = Object.values(allEvaluations);
        const avgConfidence = finalSummary?.avg_confidence ?? (evals.length ? Math.round((evals.reduce((sum, e) => sum + (e.confidence?.score || 5), 0) / evals.length) * 10) / 10 : 5.0);
        const avgEvidence = finalSummary?.avg_evidence ?? (evals.length ? Math.round((evals.reduce((sum, e) => sum + (e.evidence?.score || 5), 0) / evals.length) * 10) / 10 : 5.0);
        const avgClarity = finalSummary?.avg_clarity ?? (evals.length ? Math.round((evals.reduce((sum, e) => sum + (e.clarity?.score || 5), 0) / evals.length) * 10) / 10 : 5.0);
        const overallScore = finalSummary?.overall_score ?? (evals.length
            ? Math.round(evals.reduce((sum, e) => sum + (e.overall_score ?? 50), 0) / evals.length)
            : 50);

        const recommendation = finalSummary?.recommendation || (overallScore >= 80 ? 'STRONG FIT' : overallScore >= 60 ? 'CONSIDER' : 'NOT RECOMMENDED');

        const reportData = {
            candidate_id: candidateId,
            overall_score: overallScore,
            interview_score: overallScore,
            report: {
                overall_score: overallScore,
                avg_confidence: avgConfidence,
                avg_evidence: avgEvidence,
                avg_clarity: avgClarity,
                communication_score: finalSummary?.communication_score ?? Math.round(avgClarity * 10),
                cultural_fit_score: Math.round(overallScore),
                recommendation,
                executive_summary: finalSummary?.executive_summary || `Candidate scored ${overallScore}% across 5 responses (Confidence ${avgConfidence}/10, STAR Evidence ${avgEvidence}/10, Clarity ${avgClarity}/10).`,
                questions_evaluated: qaPayload,
                proctoring_alerts: proctorAlerts,
                strengths: finalSummary?.strengths?.length ? finalSummary.strengths : ['Completed all 5 interview questions'],
                improvement_areas: finalSummary?.improvement_areas?.length ? finalSummary.improvement_areas : ['Provide stronger STAR metrics in situational responses']
            }
        };

        try {
            await submitRound3Result(reportData);
        } catch (err) {
            console.warn('Submitted to local report state:', err);
        }

        setFinalReport(reportData.report);
        setInterviewFinished(true);
        setEvaluating(false);
    };

    const formatTime = (seconds) => {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return `${m}:${s.toString().padStart(2, '0')}`;
    };

    const currentQuestion = INTERVIEW_QUESTIONS[currentQuestionIndex];
    const progressPercent = ((currentQuestionIndex + 1) / INTERVIEW_QUESTIONS.length) * 100;
    const isProctorAlertActive = proctorStatus !== 'OK' && proctorStatus !== 'INITIALIZING';

    return (
        <div style={{ maxWidth: '1040px', margin: '0 auto', paddingBottom: '3rem' }}>
            {/* Header Stage Banner */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem', flexWrap: 'wrap', gap: '1rem' }}>
                <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px', flexWrap: 'wrap' }}>
                        <span className="badge-pill badge-active">Round 3: AI Voice & Video Interview</span>
                        <span style={{ fontSize: '0.8rem', color: '#94a3b8' }}>Session: #{candidateId?.slice(-6) || 'LIVE'}</span>
                        {!interviewFinished && (
                            <span style={{
                                fontSize: '0.75rem',
                                padding: '3px 10px',
                                borderRadius: '999px',
                                background: isProctorAlertActive ? 'rgba(239, 68, 68, 0.16)' : 'rgba(16, 185, 129, 0.14)',
                                color: isProctorAlertActive ? '#fca5a5' : '#34d399',
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: '5px',
                                fontWeight: 600
                            }}>
                                <Eye size={12} />
                                {isProctorAlertActive ? `Proctor Alert (${proctorAlerts.length})` : 'Proctoring Active'}
                            </span>
                        )}
                    </div>
                    <h1 style={{ margin: 0, fontSize: '1.85rem' }}>AI Autonomous Interview</h1>
                </div>

                {!interviewFinished && (
                    <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                        <Button variant="outline" className="btn-sm" onClick={toggleCamera}>
                            {cameraEnabled ? <VideoOff size={16} color="#f87171" /> : <Video size={16} color="#38bdf8" />}
                            <span>{cameraEnabled ? 'Disable Camera' : 'Enable Camera'}</span>
                        </Button>
                    </div>
                )}
            </div>

            {loadingSession && (
                <Card><p style={{ color: '#cbd5e1' }}>Initializing interview environment...</p></Card>
            )}

            {sessionError && (
                <Card style={{ borderColor: 'rgba(239, 68, 68, 0.4)', background: 'rgba(239, 68, 68, 0.08)' }}>
                    <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                        <AlertCircle color="#ef4444" size={24} />
                        <div>
                            <h4 style={{ color: '#f87171', margin: 0 }}>Session Notice</h4>
                            <p style={{ margin: '4px 0 0', fontSize: '0.9rem' }}>{sessionError}</p>
                        </div>
                    </div>
                </Card>
            )}

            {/* Live Webcam Visibility / Obstruction Alert Banner */}
            {!interviewFinished && isProctorAlertActive && (
                <div style={{
                    marginBottom: '1.25rem',
                    padding: '0.95rem 1.2rem',
                    borderRadius: '12px',
                    background: 'rgba(239, 68, 68, 0.16)',
                    border: '1.5px solid rgba(239, 68, 68, 0.55)',
                    color: '#fecaca',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '12px',
                    flexWrap: 'wrap'
                }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <ShieldAlert size={22} color="#f87171" />
                        <div>
                            <strong style={{ display: 'block', fontSize: '0.95rem', color: '#fca5a5' }}>
                                Live Proctoring Warning
                            </strong>
                            <span style={{ fontSize: '0.88rem' }}>{proctorMessage}</span>
                        </div>
                    </div>
                    {!cameraEnabled && (
                        <Button variant="outline" className="btn-sm" onClick={toggleCamera} style={{ borderColor: '#f87171', color: '#fecaca' }}>
                            <Video size={14} /> Enable Webcam
                        </Button>
                    )}
                </div>
            )}

            {!interviewFinished ? (
                <>
                    {/* Progress Bar */}
                    <div style={{ marginBottom: '1.5rem' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', color: '#94a3b8', marginBottom: '6px' }}>
                            <span>Question {currentQuestionIndex + 1} of {INTERVIEW_QUESTIONS.length}</span>
                            <span>{Math.round(progressPercent)}% Completed</span>
                        </div>
                        <div style={{ height: '8px', background: 'rgba(255, 255, 255, 0.08)', borderRadius: '999px', overflow: 'hidden' }}>
                            <div style={{ width: `${progressPercent}%`, height: '100%', background: 'linear-gradient(90deg, #6366f1, #38bdf8)', borderRadius: '999px', transition: 'width 0.4s ease' }} />
                        </div>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1.35fr 1fr', gap: '1.5rem', alignItems: 'start' }}>
                        <div>
                            {/* Question Card */}
                            <Card className="card-glow-accent">
                                <div style={{ fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.06em', color: '#818cf8', fontWeight: 700, marginBottom: '6px' }}>
                                    {currentQuestion.category}
                                </div>
                                <h2 style={{ fontSize: '1.3rem', lineHeight: 1.5, margin: '0 0 1rem', color: '#f8fafc' }}>
                                    {currentQuestion.question}
                                </h2>

                                {/* Microphone Controls */}
                                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap', padding: '1rem', background: 'rgba(15, 23, 42, 0.7)', borderRadius: '12px', border: '1px solid rgba(255, 255, 255, 0.06)' }}>
                                    {!isListening ? (
                                        <Button
                                            variant="primary"
                                            onClick={startListening}
                                            disabled={evaluating}
                                            style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'linear-gradient(135deg, #10b981, #059669)' }}
                                        >
                                            <Mic size={18} />
                                            <span>Start Speaking</span>
                                        </Button>
                                    ) : (
                                        <Button
                                            variant="outline"
                                            onClick={stopListening}
                                            style={{ display: 'flex', alignItems: 'center', gap: '8px', borderColor: '#ef4444', color: '#f87171', background: 'rgba(239, 68, 68, 0.1)' }}
                                        >
                                            <MicOff size={18} />
                                            <span>Stop Speaking ({formatTime(recordingDuration)})</span>
                                        </Button>
                                    )}

                                    {transcript && (
                                        <Button variant="outline" className="btn-sm" onClick={handleClearTranscript} disabled={isListening || evaluating}>
                                            <RotateCcw size={14} /> Clear & Re-record
                                        </Button>
                                    )}

                                    <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.82rem', color: isListening ? '#34d399' : '#94a3b8' }}>
                                        {isListening ? (
                                            <>
                                                <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: '#10b981', display: 'inline-block', animation: 'pulse 1.2s infinite' }}></span>
                                                <span>Live Mic Active</span>
                                            </>
                                        ) : (
                                            <span>Mic Idle</span>
                                        )}
                                    </div>
                                </div>

                                {isListening && (
                                    <div className="sound-wave-container">
                                        <div className="wave-bar"></div>
                                        <div className="wave-bar"></div>
                                        <div className="wave-bar"></div>
                                        <div className="wave-bar"></div>
                                        <div className="wave-bar"></div>
                                        <div className="wave-bar"></div>
                                        <div className="wave-bar"></div>
                                        <span style={{ marginLeft: '12px', fontSize: '0.85rem', color: '#a5b4fc', fontWeight: 600 }}>
                                            Listening in real-time... Speak your answer.
                                        </span>
                                    </div>
                                )}

                                {/* Live Transcript Display & Editable Textarea */}
                                <div style={{ marginTop: '1.25rem' }}>
                                    <label style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                                        <span style={{ fontWeight: 600, color: '#e2e8f0' }}>Your Answer Transcript:</span>
                                        <span style={{ fontSize: '0.78rem', color: '#94a3b8' }}>{transcript.split(/\s+/).filter(Boolean).length} words</span>
                                    </label>

                                    <textarea
                                        rows={6}
                                        value={transcript}
                                        onChange={(e) => {
                                            finalTranscriptRef.current = e.target.value;
                                            setTranscript(e.target.value);
                                        }}
                                        placeholder={speechSupported ? "Click 'Start Speaking' and speak your answer clearly, or type here directly..." : "Speech recognition unavailable. Type your response here..."}
                                        style={{
                                            width: '100%',
                                            padding: '1rem',
                                            lineHeight: 1.6,
                                            fontSize: '0.95rem',
                                            background: 'rgba(11, 17, 33, 0.9)',
                                            border: isListening ? '1.5px solid #6366f1' : '1px solid #1e293b',
                                            borderRadius: '12px',
                                            color: '#f8fafc',
                                            transition: 'border-color 0.2s ease'
                                        }}
                                    />
                                </div>

                                {/* Evaluation & Next Controls */}
                                <div style={{ marginTop: '1.5rem', display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
                                    {!currentEvaluation ? (
                                        <Button
                                            variant="primary"
                                            onClick={evaluateAnswer}
                                            disabled={evaluating || !transcript.trim()}
                                            style={{ minWidth: '170px' }}
                                        >
                                            {evaluating ? (
                                                <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                    <Sparkles size={16} className="spin" /> Evaluating with Gemini...
                                                </span>
                                            ) : (
                                                <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                    <Send size={16} /> Submit Answer
                                                </span>
                                            )}
                                        </Button>
                                    ) : (
                                        <Button
                                            variant="primary"
                                            onClick={handleNextQuestion}
                                            style={{ background: 'linear-gradient(135deg, #6366f1, #38bdf8)', minWidth: '180px' }}
                                        >
                                            <span>{currentQuestionIndex < INTERVIEW_QUESTIONS.length - 1 ? 'Next Question' : 'Complete Interview & View Dossier'}</span>
                                            <ArrowRight size={16} />
                                        </Button>
                                    )}
                                </div>
                            </Card>

                            {/* Live AI Feedback on submitted question */}
                            {currentEvaluation && (
                                <Card className="fade-in" style={{ borderColor: 'rgba(99, 102, 241, 0.35)', background: 'rgba(15, 23, 42, 0.9)' }}>
                                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem', flexWrap: 'wrap', gap: '8px' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                            <CheckCircle2 color={currentEvaluation.overall_score >= 70 ? '#10b981' : '#f59e0b'} size={20} />
                                            <h3 style={{ margin: 0, fontSize: '1.1rem' }}>Question {currentQuestionIndex + 1} AI Evaluation</h3>
                                        </div>
                                        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                            {currentEvaluation.model_used && (
                                                <span style={{ fontSize: '0.72rem', color: '#94a3b8' }}>{currentEvaluation.model_used}</span>
                                            )}
                                            <span className={`badge-pill ${currentEvaluation.overall_score >= 75 ? 'badge-passed' : 'badge-active'}`}>
                                                Score: {currentEvaluation.overall_score}%
                                            </span>
                                        </div>
                                    </div>

                                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '12px', marginBottom: '1rem' }}>
                                        <div style={{ background: 'rgba(255, 255, 255, 0.04)', padding: '10px 14px', borderRadius: '10px', border: '1px solid rgba(255, 255, 255, 0.06)' }}>
                                            <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>Confidence</div>
                                            <div style={{ fontSize: '1.2rem', fontWeight: 800, color: '#38bdf8' }}>{currentEvaluation.confidence?.score ?? 0} <span style={{ fontSize: '0.75rem', color: '#64748b' }}>/10</span></div>
                                        </div>
                                        <div style={{ background: 'rgba(255, 255, 255, 0.04)', padding: '10px 14px', borderRadius: '10px', border: '1px solid rgba(255, 255, 255, 0.06)' }}>
                                            <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>STAR Evidence</div>
                                            <div style={{ fontSize: '1.2rem', fontWeight: 800, color: '#10b981' }}>{currentEvaluation.evidence?.score ?? 0} <span style={{ fontSize: '0.75rem', color: '#64748b' }}>/10</span></div>
                                        </div>
                                        <div style={{ background: 'rgba(255, 255, 255, 0.04)', padding: '10px 14px', borderRadius: '10px', border: '1px solid rgba(255, 255, 255, 0.06)' }}>
                                            <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>Clarity</div>
                                            <div style={{ fontSize: '1.2rem', fontWeight: 800, color: '#a78bfa' }}>{currentEvaluation.clarity?.score ?? 0} <span style={{ fontSize: '0.75rem', color: '#64748b' }}>/10</span></div>
                                        </div>
                                    </div>

                                    <p style={{ margin: 0, fontSize: '0.88rem', color: '#cbd5e1', lineHeight: 1.6, background: 'rgba(99, 102, 241, 0.07)', padding: '10px 14px', borderRadius: '8px' }}>
                                        💬 <strong>AI Feedback:</strong> {currentEvaluation.feedback || currentEvaluation.evidence?.justification || currentEvaluation.confidence?.justification}
                                    </p>
                                </Card>
                            )}
                        </div>

                        {/* Live Webcam Proctoring Panel */}
                        <div>
                            <Card style={{
                                padding: '1rem',
                                textAlign: 'center',
                                border: isProctorAlertActive ? '1.5px solid rgba(239, 68, 68, 0.6)' : '1px solid rgba(16, 185, 129, 0.3)'
                            }}>
                                {cameraEnabled ? (
                                    <div style={{ position: 'relative', borderRadius: '12px', overflow: 'hidden', background: '#000', border: '1px solid rgba(255, 255, 255, 0.1)' }}>
                                        <video
                                            ref={videoRef}
                                            autoPlay
                                            muted
                                            playsInline
                                            style={{ width: '100%', height: '240px', objectFit: 'cover', transform: 'scaleX(-1)' }}
                                        />
                                        <div style={{
                                            position: 'absolute',
                                            top: '10px',
                                            left: '10px',
                                            background: isProctorAlertActive ? 'rgba(127, 29, 29, 0.85)' : 'rgba(0,0,0,0.65)',
                                            padding: '4px 10px',
                                            borderRadius: '6px',
                                            fontSize: '0.74rem',
                                            color: isProctorAlertActive ? '#fecaca' : '#34d399',
                                            display: 'flex',
                                            alignItems: 'center',
                                            gap: '6px'
                                        }}>
                                            <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: isProctorAlertActive ? '#ef4444' : '#10b981' }}></span>
                                            {proctorStatus === 'OK' ? 'Face Visible • Live' : proctorStatus.replace('_', ' ')}
                                        </div>
                                    </div>
                                ) : (
                                    <div style={{ padding: '3rem 1rem', borderRadius: '12px', background: 'rgba(15, 23, 42, 0.8)', border: '1px dashed rgba(239, 68, 68, 0.45)', color: '#fca5a5' }}>
                                        <VideoOff size={34} style={{ margin: '0 auto 10px' }} />
                                        <div style={{ fontWeight: 700, fontSize: '0.92rem' }}>Webcam Disabled</div>
                                        <p style={{ fontSize: '0.8rem', color: '#94a3b8', marginTop: '6px' }}>Enable camera to stay compliant with live interview monitoring.</p>
                                    </div>
                                )}

                                <div style={{ marginTop: '0.75rem', fontSize: '0.8rem', color: isProctorAlertActive ? '#fca5a5' : '#94a3b8' }}>
                                    {proctorMessage}
                                </div>

                                {proctorAlerts.length > 0 && (
                                    <div style={{ marginTop: '0.85rem', textAlign: 'left', background: 'rgba(239, 68, 68, 0.08)', borderRadius: '8px', padding: '8px 10px', maxHeight: '110px', overflowY: 'auto' }}>
                                        <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#fca5a5', marginBottom: '4px' }}>
                                            Proctoring Log ({proctorAlerts.length})
                                        </div>
                                        {proctorAlerts.slice(-3).map((a, i) => (
                                            <div key={i} style={{ fontSize: '0.74rem', color: '#cbd5e1' }}>
                                                [{a.timestamp}] {a.code.replace('_', ' ')}
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </Card>
                        </div>
                    </div>
                </>
            ) : (
                /* INTERVIEW COMPLETED REPORT */
                <Card className="fade-in card-glow-accent">
                    <div style={{ textAlign: 'center', padding: '1.5rem 0' }}>
                        <div style={{ width: '64px', height: '64px', borderRadius: '50%', background: 'rgba(16, 185, 129, 0.15)', color: '#10b981', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '0 auto 1rem' }}>
                            <Award size={36} />
                        </div>
                        <h2 style={{ fontSize: '2rem', marginBottom: '8px' }}>Round 3 Interview Completed</h2>
                        <p style={{ color: '#cbd5e1', maxWidth: '560px', margin: '0 auto 1.5rem' }}>
                            Your AI speech, video presence, and technical competency analysis has been finalized and integrated into your comprehensive application dossier.
                        </p>

                        <div className="score-circle" style={{ '--percentage': `${finalReport?.overall_score || 0}%`, color: (finalReport?.overall_score || 0) >= 80 ? '#10b981' : '#f59e0b' }}>
                            <span className="score-value">{Math.round(finalReport?.overall_score || 0)}%</span>
                        </div>

                        <div style={{ display: 'inline-block', marginBottom: '2rem' }}>
                            <span className={`badge-pill ${(finalReport?.overall_score || 0) >= 80 ? 'badge-passed' : 'badge-failed'}`} style={{ fontSize: '0.9rem', padding: '6px 16px' }}>
                                Verdict: {finalReport?.recommendation || 'EVALUATED'}
                            </span>
                        </div>

                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem', textAlign: 'left', marginBottom: '2rem' }}>
                            <div style={{ background: 'rgba(15, 23, 42, 0.8)', padding: '1.2rem', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.06)' }}>
                                <div style={{ color: '#94a3b8', fontSize: '0.8rem', textTransform: 'uppercase' }}>Clarity Score</div>
                                <div style={{ fontSize: '1.6rem', fontWeight: 800, color: '#38bdf8' }}>{finalReport?.communication_score ?? 0}%</div>
                                <small style={{ color: '#64748b' }}>Technical precision & articulation</small>
                            </div>
                            <div style={{ background: 'rgba(15, 23, 42, 0.8)', padding: '1.2rem', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.06)' }}>
                                <div style={{ color: '#94a3b8', fontSize: '0.8rem', textTransform: 'uppercase' }}>Vocal Confidence</div>
                                <div style={{ fontSize: '1.6rem', fontWeight: 800, color: '#10b981' }}>{finalReport?.avg_confidence ?? 0}/10</div>
                                <small style={{ color: '#64748b' }}>Speech consistency & conviction</small>
                            </div>
                            <div style={{ background: 'rgba(15, 23, 42, 0.8)', padding: '1.2rem', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.06)' }}>
                                <div style={{ color: '#94a3b8', fontSize: '0.8rem', textTransform: 'uppercase' }}>STAR Evidence Depth</div>
                                <div style={{ fontSize: '1.6rem', fontWeight: 800, color: '#818cf8' }}>{finalReport?.avg_evidence ?? 0}/10</div>
                                <small style={{ color: '#64748b' }}>Scenario validation & metrics</small>
                            </div>
                        </div>

                        <div style={{ textAlign: 'left', background: 'rgba(15, 23, 42, 0.85)', padding: '1.5rem', borderRadius: '12px', border: '1px solid rgba(255, 255, 255, 0.08)', marginBottom: '2rem' }}>
                            <h4 style={{ color: '#a5b4fc', marginTop: 0 }}>Executive AI Assessment</h4>
                            <p style={{ color: '#e2e8f0', lineHeight: 1.7, fontSize: '0.95rem' }}>
                                {finalReport?.executive_summary}
                            </p>

                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginTop: '1rem' }}>
                                <div>
                                    <h5 style={{ color: '#34d399', margin: '0 0 6px' }}>Key Strengths:</h5>
                                    <ul style={{ margin: 0, paddingLeft: '1.2rem', fontSize: '0.88rem', color: '#94a3b8' }}>
                                        {finalReport?.strengths?.map((s, i) => <li key={i}>{s}</li>)}
                                    </ul>
                                </div>
                                <div>
                                    <h5 style={{ color: '#f59e0b', margin: '0 0 6px' }}>Refinement Areas:</h5>
                                    <ul style={{ margin: 0, paddingLeft: '1.2rem', fontSize: '0.88rem', color: '#94a3b8' }}>
                                        {finalReport?.improvement_areas?.map((a, i) => <li key={i}>{a}</li>)}
                                    </ul>
                                </div>
                            </div>
                        </div>

                        <div style={{ display: 'flex', gap: '1rem', justifyContent: 'center' }}>
                            <Button variant="primary" onClick={() => navigate('/recruiter')} style={{ padding: '0.85rem 2rem' }}>
                                📊 View Candidate Pool in Recruiter Console
                            </Button>
                            <Button variant="outline" onClick={() => navigate('/')}>
                                🏠 Return to Home
                            </Button>
                        </div>
                    </div>
                </Card>
            )}
        </div>
    );
}
