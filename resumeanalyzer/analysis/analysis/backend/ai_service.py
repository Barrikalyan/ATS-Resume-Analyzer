"""
AI Service for Round 3 Voice Interview System
Integrates with Google Gemini Flash (primary) and Groq (fallback) for question generation and answer evaluation.
"""
import os
import json
import re
import threading
import requests
from pathlib import Path
from dotenv import load_dotenv

try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover
    genai = None
    types = None

try:
    from prompts import (
        SYSTEM_PROMPT,
        get_question_generation_prompt,
    )
except ModuleNotFoundError:  # support running as a package
    from .prompts import (
        SYSTEM_PROMPT,
        get_question_generation_prompt,
    )


def _find_project_root(start_dir: Path) -> Path:
    for candidate in [start_dir, *start_dir.parents]:
        if (candidate / ".env").exists():
            return candidate
    return start_dir


ROOT_DIR = _find_project_root(Path(__file__).resolve().parent)
load_dotenv(ROOT_DIR / ".env", override=False)
load_dotenv(Path(__file__).resolve().parent / ".env", override=False)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
AI_TIMEOUT_SECONDS = float(os.getenv("INTERVIEW_AI_TIMEOUT_SECONDS", "20"))

GEMINI_FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
]

GROQ_FALLBACK_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
]


def _get_gemini_candidates() -> list[str]:
    preferred = (os.getenv("GEMINI_MODEL_ID") or "").strip()
    if preferred.startswith("models/"):
        preferred = preferred.split("/", 1)[1]
    broken = {"gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.6-flash"}
    ordered = []
    if preferred and preferred not in broken:
        ordered.append(preferred)
    for m in GEMINI_FALLBACK_MODELS:
        if m not in ordered:
            ordered.append(m)
    return ordered


def _get_groq_candidates() -> list[str]:
    preferred = (os.getenv("GROQ_MODEL_ID") or "").strip()
    ordered = []
    if preferred:
        ordered.append(preferred)
    for m in GROQ_FALLBACK_MODELS:
        if m not in ordered:
            ordered.append(m)
    return ordered


_gemini_client = (
    genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=int(AI_TIMEOUT_SECONDS * 1000)),
    )
    if (genai is not None and GEMINI_API_KEY)
    else None
)


def _extract_json(raw_text: str) -> dict:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    match = re.search(r"(\{.*\})", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    return json.loads(text)


def _call_llm_text(prompt: str, json_mode: bool = False) -> tuple[str, str]:
    """Call Gemini Flash (primary) and fall back to Groq models if Gemini fails."""
    last_error = None

    if _gemini_client is not None:
        for model_id in _get_gemini_candidates():
            result = {}
            failure = {}

            def _run():
                try:
                    cfg = types.GenerateContentConfig(response_mime_type="application/json") if json_mode else None
                    resp = _gemini_client.models.generate_content(
                        model=model_id,
                        contents=prompt,
                        config=cfg,
                    )
                    result["text"] = resp.text
                except Exception as exc:
                    failure["err"] = exc

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(AI_TIMEOUT_SECONDS)
            if not t.is_alive() and "err" not in failure and result.get("text"):
                return result["text"], model_id
            last_error = failure.get("err") or TimeoutError(f"Gemini {model_id} timed out")
            print(f"Interview Gemini ({model_id}) notice: {last_error}")

    if GROQ_API_KEY:
        for gm in _get_groq_candidates():
            try:
                payload = {
                    "model": gm,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.25,
                }
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=AI_TIMEOUT_SECONDS,
                )
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"], f"groq:{gm}"
                last_error = RuntimeError(f"Groq {gm} status {resp.status_code}")
            except Exception as exc:
                last_error = exc
                print(f"Interview Groq ({gm}) notice: {exc}")

    raise RuntimeError(f"All LLM providers failed: {last_error}")


def _heuristic_evaluate_answer(question: str, answer_text: str) -> dict:
    """Honest, differentiated fallback scorer when external LLMs are unavailable."""
    clean = (answer_text or "").strip()
    words = re.findall(r"[a-zA-Z0-9_+#.-]+", clean.lower())
    word_count = len(words)

    evasive_phrases = ["i don't know", "not sure", "no idea", "skip", "pass", "nothing to say", "idk"]
    if word_count < 5 or any(p in clean.lower() for p in evasive_phrases):
        return {
            "confidence": {"score": 3.0, "justification": "Response is too brief or uncertain to demonstrate confidence.", "key_phrases": []},
            "evidence": {"score": 2.0, "justification": "No concrete example, project, or outcome provided.", "key_phrases": []},
            "clarity": {"score": 3.5, "justification": "Answer lacks elaboration and structured explanation.", "key_phrases": []},
            "arrogance": {"score": 0.5, "justification": "Neutral demeanor.", "key_phrases": []},
            "overall_score": 28,
            "feedback": "Answer was too brief or lacked concrete evidence. Explain the situation, your specific actions, and the measurable result.",
            "model_used": "local-rubric",
        }

    q_tokens = set(re.findall(r"[a-z]{4,}", (question or "").lower()))
    a_tokens = set(words)
    overlap = len(q_tokens & a_tokens)

    star_terms = {"situation", "task", "action", "result", "impact", "team", "built", "designed", "implemented", "debugged", "resolved", "improved", "scaled", "reduced", "increased", "learned", "because"}
    star_hits = len(star_terms & a_tokens)
    has_numbers = bool(re.search(r"\d+%|\d+x|\d+\s*(ms|sec|hours|days|users|services|bugs)", clean.lower()))

    confidence = min(9.5, 4.5 + min(word_count / 18.0, 3.2) + (1.0 if star_hits >= 2 else 0.3))
    evidence = min(9.6, 3.2 + min(star_hits * 1.1, 4.2) + (1.5 if has_numbers else 0.0) + min(overlap * 0.3, 1.0))
    clarity = min(9.5, 4.5 + min(word_count / 20.0, 3.0) + min(overlap * 0.4, 1.5))
    arrogance = 2.0 if any(w in clean.lower() for w in ["i alone", "everyone else was wrong", "obviously", "easy for me"]) else 0.6

    overall = round(max(20, min(95, ((confidence + evidence + clarity) / 3.0) * 10 - arrogance * 1.5)))

    return {
        "confidence": {"score": round(confidence, 1), "justification": f"Provided {word_count} words with {'steady' if word_count >= 25 else 'limited'} elaboration.", "key_phrases": []},
        "evidence": {"score": round(evidence, 1), "justification": f"Detected {star_hits} STAR/action indicators{' and measurable metrics' if has_numbers else ''}.", "key_phrases": []},
        "clarity": {"score": round(clarity, 1), "justification": "Relevance and structure aligned with the prompt." if overlap >= 2 else "Could address the core question more directly.", "key_phrases": []},
        "arrogance": {"score": round(arrogance, 1), "justification": "Professional tone.", "key_phrases": []},
        "overall_score": overall,
        "feedback": "Good structured example with clear outcomes." if overall >= 80 else "Add concrete STAR details (Situation, Action, measurable Result) to raise your score.",
        "model_used": "local-rubric",
    }


class AIService:
    """Core AI service for interview operations"""

    @staticmethod
    def generate_questions(resume_skills: str, job_description: str, domain: str) -> list[str]:
        try:
            prompt = get_question_generation_prompt(resume_skills, job_description, domain)
            questions_text, _ = _call_llm_text(prompt, json_mode=False)
            questions = []
            for line in questions_text.split("\n"):
                match = re.match(r"^\d+[\.\)]\s*(.+)$", line.strip())
                if match:
                    questions.append(match.group(1).strip())
            if len(questions) >= 5:
                return questions[:5]
            raise ValueError(f"Expected 5 questions, got {len(questions)}")
        except Exception as e:
            print(f"Error generating questions: {e}")
            return [
                f"Can you describe a challenging project you worked on in {domain}?",
                f"What are your strongest technical skills relevant to {domain}?",
                "How do you stay updated with the latest technologies in your field?",
                "Describe a time when you had to debug a complex issue. How did you approach it?",
                "What interests you most about this role and how does it align with your career goals?",
            ]

    @staticmethod
    def evaluate_answer(question: str, answer_text: str, resume_summary: str = "") -> dict:
        """
        Evaluate a single interview answer honestly and differentiate quality using Gemini Flash.
        """
        clean_answer = (answer_text or "").strip()
        if len(clean_answer.split()) < 4:
            return _heuristic_evaluate_answer(question, clean_answer)

        prompt = f"""You are a strict, fair senior technical & behavioral interviewer evaluating a candidate's spoken answer.

Question Asked:
"{question}"

Candidate Spoken Answer Transcript:
"{clean_answer}"

Candidate Resume Context (if any):
"{(resume_summary or '')[:600]}"

Evaluate the answer genuinely based ONLY on what the candidate actually said. Do NOT give inflated default scores.
Rubric for `overall_score` (0-100):
- 0-40: Off-topic, evasive ("I don't know"), gibberish, or under ~12 words with no substance.
- 41-64: Vague/generic answer with buzzwords but no concrete situation, action, or technical reasoning.
- 65-79: Solid, relevant answer with decent clarity, but missing measurable outcomes or deeper tradeoffs.
- 80-95: Strong, specific STAR-format or technically rigorous answer directly answering the question with clear actions and results.

Return ONLY valid JSON matching this exact schema:
{{
  "confidence": {{
    "score": 0.0-10.0,
    "justification": "1 concise sentence citing actual tone/decisiveness",
    "key_phrases": ["1-2 short phrases from answer"]
  }},
  "evidence": {{
    "score": 0.0-10.0,
    "justification": "1 concise sentence evaluating STAR depth, technical specifics, and measurable outcomes",
    "key_phrases": ["1-2 short phrases from answer"]
  }},
  "clarity": {{
    "score": 0.0-10.0,
    "justification": "1 concise sentence on structure, relevance, and articulation",
    "key_phrases": ["1-2 short phrases from answer"]
  }},
  "arrogance": {{
    "score": 0.0-10.0,
    "justification": "1 concise sentence (0-2 = humble/collaborative, 6+ = dismissive/ego)",
    "key_phrases": []
  }},
  "overall_score": 0-100,
  "feedback": "1-2 actionable sentences pointing out what worked or what flaw lowered the score."
}}"""

        try:
            raw, model_used = _call_llm_text(prompt, json_mode=True)
            evaluation = _extract_json(raw)

            for param in ["confidence", "evidence", "clarity", "arrogance"]:
                if param not in evaluation or not isinstance(evaluation[param], dict):
                    raise ValueError(f"Missing parameter: {param}")
                score_val = float(evaluation[param].get("score", 5.0))
                evaluation[param]["score"] = round(max(0.0, min(10.0, score_val)), 1)
                evaluation[param].setdefault("justification", "")
                evaluation[param].setdefault("key_phrases", [])

            if "overall_score" not in evaluation or evaluation["overall_score"] is None:
                c = evaluation["confidence"]["score"]
                e = evaluation["evidence"]["score"]
                cl = evaluation["clarity"]["score"]
                a = evaluation["arrogance"]["score"]
                evaluation["overall_score"] = round(max(0, min(100, ((c + e + cl) / 3.0) * 10 - a * 1.5)))
            else:
                evaluation["overall_score"] = round(max(0, min(100, float(evaluation["overall_score"]))))

            evaluation["feedback"] = evaluation.get("feedback") or evaluation["evidence"]["justification"]
            evaluation["model_used"] = model_used
            return evaluation
        except Exception as exc:
            print(f"Error evaluating answer via LLM: {exc}")
            return _heuristic_evaluate_answer(question, clean_answer)

    @staticmethod
    def calculate_final_score(evaluations: list[dict], domain: str, qa_records: list[dict] | None = None, proctoring_alerts: int = 0) -> dict:
        if not evaluations:
            return {
                "avg_confidence": 0,
                "avg_evidence": 0,
                "avg_clarity": 0,
                "avg_arrogance": 0,
                "overall_score": 0,
                "recommendation": "REJECT",
                "explanation": "No evaluations available",
                "improvement_areas": [],
                "strengths": [],
            }

        avg_confidence = sum(float(e["confidence"]["score"]) for e in evaluations) / len(evaluations)
        avg_evidence = sum(float(e["evidence"]["score"]) for e in evaluations) / len(evaluations)
        avg_clarity = sum(float(e["clarity"]["score"]) for e in evaluations) / len(evaluations)
        avg_arrogance = sum(float(e["arrogance"]["score"]) for e in evaluations) / len(evaluations)

        per_q_scores = [float(e.get("overall_score", ((e["confidence"]["score"] + e["evidence"]["score"] + e["clarity"]["score"]) / 3.0) * 10)) for e in evaluations]
        base_overall = sum(per_q_scores) / len(per_q_scores)
        # Small arrogance/proctoring penalty only when excessive
        penalty = max(0.0, (avg_arrogance - 3.0) * 2.0) + min(proctoring_alerts * 1.5, 8.0)
        overall_score = round(max(0.0, min(100.0, base_overall - penalty)), 1)

        recommendation = "STRONG FIT" if overall_score >= 80 else ("CONSIDER" if overall_score >= 60 else "NOT RECOMMENDED")

        # Try Gemini Flash to synthesize tailored summary/strengths/improvements
        if qa_records:
            summary_prompt = f"""Summarize this Round 3 interview for role '{domain}'.
Average scores: Confidence {avg_confidence:.1f}/10, STAR Evidence {avg_evidence:.1f}/10, Clarity {avg_clarity:.1f}/10, Overall {overall_score}%.
Proctoring alerts logged: {proctoring_alerts}.
Q&A evaluations: {json.dumps(qa_records)[:4500]}

Return ONLY JSON:
{{
  "executive_summary": "2-3 sentences grounded in the candidate's actual answers",
  "strengths": ["2-3 specific strengths"],
  "improvement_areas": ["2-3 specific improvements"]
}}"""
            try:
                raw, model_used = _call_llm_text(summary_prompt, json_mode=True)
                parsed = _extract_json(raw)
                return {
                    "avg_confidence": round(avg_confidence, 1),
                    "avg_evidence": round(avg_evidence, 1),
                    "avg_clarity": round(avg_clarity, 1),
                    "avg_arrogance": round(avg_arrogance, 1),
                    "overall_score": overall_score,
                    "recommendation": recommendation,
                    "explanation": parsed.get("executive_summary", ""),
                    "strengths": parsed.get("strengths", []),
                    "improvement_areas": parsed.get("improvement_areas", []),
                    "model_used": model_used,
                }
            except Exception as exc:
                print(f"Final interview summary fallback: {exc}")

        return {
            "avg_confidence": round(avg_confidence, 1),
            "avg_evidence": round(avg_evidence, 1),
            "avg_clarity": round(avg_clarity, 1),
            "avg_arrogance": round(avg_arrogance, 1),
            "overall_score": overall_score,
            "recommendation": recommendation,
            "explanation": f"Candidate achieved {overall_score}% overall across {len(evaluations)} responses (Confidence {avg_confidence:.1f}/10, STAR Evidence {avg_evidence:.1f}/10, Clarity {avg_clarity:.1f}/10).",
            "strengths": [
                f"Clarity average of {avg_clarity:.1f}/10" if avg_clarity >= 7 else "Completed all interview prompts",
                f"Strong STAR evidence ({avg_evidence:.1f}/10)" if avg_evidence >= 7.5 else f"Confidence rated {avg_confidence:.1f}/10",
            ],
            "improvement_areas": [
                "Use clear Situation-Task-Action-Result structure with measurable outcomes" if avg_evidence < 8 else "Continue quantifying architecture & business trade-offs",
                "Eliminate camera visibility/focus interruptions during interview" if proctoring_alerts > 0 else "Maintain concise pacing on technical deep-dives",
            ],
            "model_used": "aggregated",
        }


def extract_resume_text(file_path: str) -> str:
    try:
        if file_path.endswith(".pdf"):
            from PyPDF2 import PdfReader
            reader = PdfReader(file_path)
            return "".join(page.extract_text() or "" for page in reader.pages)
        if file_path.endswith(".docx"):
            from docx import Document
            doc = Document(file_path)
            return "\n".join(para.text for para in doc.paragraphs)
        return ""
    except Exception as e:
        print(f"Error extracting resume text: {e}")
        return ""
