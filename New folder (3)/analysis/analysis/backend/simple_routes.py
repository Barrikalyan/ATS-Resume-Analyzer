"""
Simplified API routes for direct Round 3 answer & final interview evaluation
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Dict, Optional, Any

try:
    from ai_service import AIService
except ModuleNotFoundError:  # support running as a package
    from .ai_service import AIService

router = APIRouter(prefix="/api", tags=["evaluation"])


class AnswerEvaluationRequest(BaseModel):
    question: str
    answer: str
    question_number: int
    resume_summary: Optional[str] = ""


class FinalEvaluationRequest(BaseModel):
    domain: Optional[str] = "Engineering"
    questions_evaluated: List[Dict[str, Any]]
    proctoring_alerts: Optional[int] = 0


@router.post("/evaluate-answer")
def evaluate_answer(request: AnswerEvaluationRequest):
    """
    Evaluate a single answer on Confidence, Evidence, Clarity, Arrogance, and overall_score using Gemini Flash.
    """
    try:
        return AIService.evaluate_answer(
            question=request.question,
            answer_text=request.answer,
            resume_summary=request.resume_summary or "",
        )
    except Exception as e:
        print(f"evaluate_answer fallback error: {e}")
        return {
            "confidence": {"score": 4.0, "justification": "Unable to evaluate cleanly; fallback applied.", "key_phrases": []},
            "evidence": {"score": 4.0, "justification": "Unable to evaluate cleanly; fallback applied.", "key_phrases": []},
            "clarity": {"score": 4.0, "justification": "Unable to evaluate cleanly; fallback applied.", "key_phrases": []},
            "arrogance": {"score": 0.5, "justification": "Neutral.", "key_phrases": []},
            "overall_score": 40,
            "feedback": "Fallback score applied due to evaluator error.",
        }


@router.post("/evaluate-final")
def evaluate_final(request: FinalEvaluationRequest):
    """
    Aggregate and synthesize the final Round 3 interview report from all evaluated answers.
    """
    evals = []
    qa_records = []
    for item in request.questions_evaluated:
        ev = item.get("evaluation") or {}
        if ev and "confidence" in ev:
            evals.append(ev)
        qa_records.append({
            "question": item.get("question", ""),
            "answer": item.get("answer", ""),
            "overall_score": ev.get("overall_score"),
            "feedback": ev.get("feedback"),
        })

    result = AIService.calculate_final_score(
        evaluations=evals,
        domain=request.domain or "Engineering",
        qa_records=qa_records,
        proctoring_alerts=int(request.proctoring_alerts or 0),
    )
    return {
        "overall_score": result["overall_score"],
        "avg_confidence": result["avg_confidence"],
        "avg_evidence": result["avg_evidence"],
        "avg_clarity": result["avg_clarity"],
        "avg_arrogance": result["avg_arrogance"],
        "communication_score": round(result["avg_clarity"] * 10),
        "recommendation": result["recommendation"],
        "executive_summary": result["explanation"],
        "strengths": result.get("strengths", []),
        "improvement_areas": result.get("improvement_areas", []),
        "model_used": result.get("model_used", "gemini-flash"),
    }
