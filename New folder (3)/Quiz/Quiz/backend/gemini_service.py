import os
import json
import uuid
import random
import re
import httpx
import ssl
import requests
from pathlib import Path
from typing import List, Dict, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types

def _find_project_root(start_dir: Path) -> Path:
    for candidate in [start_dir, *start_dir.parents]:
        if (candidate / ".env").exists():
            return candidate
    return start_dir

ROOT_DIR = _find_project_root(Path(__file__).resolve().parent)
load_dotenv(ROOT_DIR / ".env", override=False)

# AGGRESSIVE SSL BYPASS for corporate firewalls
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# Configure Groq (Primary for Round 2) & Gemini (Multi-model Fallback)
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
MOCK_AI_VALUE = os.environ.get("MOCK_AI")
if MOCK_AI_VALUE is None:
    MOCK_AI = not (bool(API_KEY) or bool(GROQ_API_KEY))
else:
    MOCK_AI = MOCK_AI_VALUE.strip().lower() == "true"
ALLOW_LOCAL_FALLBACK = os.environ.get("QUIZ_ALLOW_LOCAL_FALLBACK", "true").strip().lower() != "false"
if not (API_KEY or GROQ_API_KEY) and not MOCK_AI and APP_ENV == "production":
    raise RuntimeError("GROQ_API_KEY or GEMINI_API_KEY is required for production quiz integration")

# Patch httpx which genai uses under the hood
original_httpx_client = httpx.Client
class UnverifiedClient(original_httpx_client):
    def __init__(self, *args, **kwargs):
        kwargs['verify'] = False
        super().__init__(*args, **kwargs)
httpx.Client = UnverifiedClient

client = (
    genai.Client(
        api_key=API_KEY,
        http_options=types.HttpOptions(timeout=120000),
    )
    if API_KEY
    else None
)

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


def _resolve_model_id(candidate: Optional[str] = None) -> str:
    raw = (candidate or os.environ.get("GEMINI_MODEL_ID") or "gemini-3.8-flash").strip()
    if not raw:
        return "gemini-3.8-flash"
    if raw.startswith("models/"):
        raw = raw.split("/", 1)[1]
    deprecated = {"gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.6-flash"}
    if raw in deprecated:
        return "gemini-3.8-flash"
    return raw


def _get_gemini_candidates() -> List[str]:
    primary = _resolve_model_id()
    ordered = [primary]
    for m in GEMINI_FALLBACK_MODELS:
        if m not in ordered:
            ordered.append(m)
    return ordered


def _get_groq_candidates() -> List[str]:
    preferred = os.environ.get("GROQ_MODEL_ID", "").strip()
    ordered = []
    if preferred:
        ordered.append(preferred)
    for m in GROQ_FALLBACK_MODELS:
        if m not in ordered:
            ordered.append(m)
    return ordered


MODEL_ID = _resolve_model_id()


def _call_llm_json(prompt: str, timeout_seconds: int = 60):
    """Try Groq models first (Round 2 primary), then fall back across Gemini Flash models."""
    last_err = None

    if GROQ_API_KEY:
        for gm in _get_groq_candidates():
            try:
                resp = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": gm,
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.4,
                    },
                    timeout=timeout_seconds,
                )
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"]
                    return _parse_json_response(content)
                last_err = RuntimeError(f"Groq {gm} status {resp.status_code}: {resp.text[:200]}")
                print(f"WARNING: Groq ({gm}) failed ({resp.status_code}); trying next fallback model...")
            except Exception as exc:
                last_err = exc
                print(f"WARNING: Groq ({gm}) error: {exc}")

    if client is not None:
        for gem_model in _get_gemini_candidates():
            try:
                response = client.models.generate_content(
                    model=gem_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        max_output_tokens=32768,
                    ),
                )
                return _parse_json_response(response.text)
            except Exception as exc:
                last_err = exc
                print(f"WARNING: Gemini ({gem_model}) failed: {exc}")

    if last_err is not None:
        raise last_err
    raise RuntimeError("No LLM provider configured (GROQ_API_KEY and GEMINI_API_KEY are missing)")


def _normalize_context(context: Optional[dict]) -> dict:
    if not isinstance(context, dict):
        return {}
    cleaned = {}
    for key in [
        "candidate_id",
        "role",
        "resume_preview",
        "resume_text",
        "matched_skills",
        "missing_skills",
        "projects",
        "experience",
        "education",
        "relevant_technologies",
    ]:
        value = context.get(key)
        if value not in (None, ""):
            cleaned[key] = value
    return cleaned


def _difficulty_points(difficulty: str) -> int:
    mapping = {"easy": 5, "medium": 10, "hard": 15}
    return mapping.get(str(difficulty).lower(), 5)


def _normalize_question(question: dict, index: int = 1) -> dict:
    if not isinstance(question, dict):
        raise ValueError("Invalid question payload")
    qtype = str(question.get("type", "mcq")).strip().lower()
    question_type = qtype if qtype in {
        "mcq",
        "multiple_select",
        "fill_blank",
        "code_snippet",
        "code_debugging",
        "output_prediction",
        "sql",
        "scenario",
        "conceptual",
        "dsa",
    } else "mcq"
    question_text = str(question.get("question", "")).strip()
    if not question_text:
        raise ValueError("Question text is required")
    difficulty = str(question.get("difficulty", "easy")).strip().lower()
    if difficulty not in {"easy", "medium", "hard"}:
        difficulty = "easy"
    points = int(question.get("points") or _difficulty_points(difficulty))
    options = question.get("options")
    if options is None:
        options = []
    if not isinstance(options, list):
        options = []
    if question_type in {"mcq", "multiple_select"} and len(options) < 2:
        raise ValueError(f"Question {index} missing options")
    answer_value = question.get("correctAnswer")
    if answer_value is None:
        answer_value = question.get("answer")
    if answer_value is None:
        raise ValueError(f"Question {index} missing correctAnswer")
    explanation = str(question.get("explanation", "")).strip()
    clean_question = {
        "id": int(question.get("id", index)),
        "difficulty": difficulty,
        "type": question_type,
        "question": question_text,
        "options": options,
        "correctAnswer": answer_value,
        "explanation": explanation,
        "points": points,
    }
    return clean_question


def _validate_question_set(data: dict) -> list:
    if not isinstance(data, dict):
        raise ValueError("AI returned a non-object payload")
    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) < 20:
        raise ValueError("AI returned insufficient questions — expected 20")
    cleaned = []
    seen_ids = set()
    seen_questions = set()
    for idx, question in enumerate(questions, start=1):
        normalized = _normalize_question(question, idx)
        question_text = str(normalized.get("question", "")).strip()
        if question_text in seen_questions:
            continue
        if normalized["id"] in seen_ids:
            normalized["id"] = len(cleaned) + 1
        seen_ids.add(normalized["id"])
        seen_questions.add(question_text)
        cleaned.append(normalized)
    if len(cleaned) < 5:
        raise ValueError("AI returned too few unique valid questions")
    if len(cleaned) != len(questions):
        # This is expected if Gemini repeated questions or emitted duplicates; the round still proceeds with the valid subset.
        pass
    difficulty_counts = {"easy": 0, "medium": 0, "hard": 0}
    for q in cleaned:
        difficulty_counts[q["difficulty"]] += 1
    if max(difficulty_counts.values()) > len(cleaned) * 0.5:
        raise ValueError("Difficulty distribution invalid")
    return cleaned


def _parse_json_response(raw_text: str):
    if raw_text is None:
        raise ValueError("Empty Gemini response")
    text = str(raw_text).strip()
    # strip markdown fences and leading language hints
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].lstrip()
    text = text.strip()

    # Gemini can emit literal newlines or tabs inside JSON string values.
    # Escape those controls only while inside a quoted string.
    repaired = []
    in_string = False
    escaped = False
    for character in text:
        if escaped:
            repaired.append(character)
            escaped = False
            continue
        if character == "\\":
            repaired.append(character)
            escaped = True
            continue
        if character == '"':
            repaired.append(character)
            in_string = not in_string
            continue
        if in_string and character == "\n":
            repaired.append("\\n")
        elif in_string and character == "\r":
            repaired.append("\\r")
        elif in_string and character == "\t":
            repaired.append("\\t")
        elif ord(character) < 32:
            continue
        else:
            repaired.append(character)
    text = "".join(repaired)

    # First attempt: direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Second attempt: extract the first JSON object/array-looking substring
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if m:
        candidate = m.group(1)
        try:
            return json.loads(candidate)
        except Exception:
            # fall through to next attempt
            pass

    # Third attempt: remove non-ascii characters and try again
    candidate = text.encode('ascii', 'ignore').decode('ascii')
    try:
        return json.loads(candidate)
    except Exception as e:
        # Provide a helpful error without exposing sensitive data
        raise ValueError(f"Failed to parse JSON response from Gemini (len={len(text)}). Error: {str(e)}")


def _is_quota_or_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        '429' in text
        or 'resource_exhausted' in text
        or 'quota' in text
        or 'rate limit' in text
        or 'rate_limit' in text
    )


def sanitize_public_question(question: dict) -> dict:
    public_question = dict(question)
    public_question["question"] = re.sub(
        r"\s*\(\s*(?:variant\s+\d+|Q\s*#\s*\d+|question\s*#?\s*\d+)\s*\)\s*",
        " ",
        str(public_question.get("question", "")),
        flags=re.IGNORECASE,
    )
    public_question["question"] = re.sub(
        r"\s*(?:Q\s*#\s*\d+|variant\s+\d+|question\s*#?\s*\d+)\s*",
        " ",
        public_question["question"],
        flags=re.IGNORECASE,
    ).strip()
    public_question.pop("correctAnswer", None)
    public_question.pop("explanation", None)
    public_question.pop("internal_prompt", None)
    public_question.pop("grading_hint", None)
    return public_question


def sanitize_public_report(report: dict) -> dict:
    safe_report = dict(report)
    safe_report["results"] = []
    for result in report.get("results", []):
        safe_result = dict(result)
        safe_question = sanitize_public_question({"question": safe_result.get("question", "")})
        safe_result["question"] = safe_question["question"]
        safe_result.pop("correctAnswer", None)
        safe_result.pop("explanation", None)
        safe_report["results"].append(safe_result)
    return safe_report


def generate_single_question(field: str, difficulty: str, excluded_questions: list, qtype: str = None):
    # qtype (optional) lets callers request a specific question type (mcq, sql, code_snippet, etc.)
    requested_type = str(qtype or 'mcq').lower()
    type_hint = f"Type: \"{requested_type}\". MUST generate this exact type and nothing else."
    prompt = f"""
    You are an expert technical interviewer.
    Generate exactly ONE real technical question for the field: "{field}".
    Difficulty: "{difficulty}" (Easy: 5 pts, Medium: 10 pts, Hard: 15 pts).
    
    {type_hint}
    
    The question must be technically meaningful and role-specific for "{field}".
    It must not be a placeholder or a generic filler question.
    It must not be a repeated or near-duplicate of any excluded question.
    
    Allowed types:
    - mcq
    - sql
    - output_prediction
    - code_debugging
    - scenario
    - conceptual
    - code_snippet
    - dsa
    
    Excluded Questions (to ensure uniqueness):
    {json.dumps(excluded_questions)}
 
    Output Format (JSON):
    {{
      "id": string_or_int,
      "difficulty": "{difficulty}",
      "type": "{requested_type}",
      "question": "Role-specific technical question text here",
      "options": ["Option A", "Option B", "Option C", "Option D"], 
      "correctAnswer": "The correct answer or code",
      "explanation": "Brief explanation of the correct answer",
      "points": 5|10|15
    }}
    """
    
    if MOCK_AI and client is None and not GROQ_API_KEY:
        return get_mock_single_question(field, difficulty, qtype)
    try:
        return _call_llm_json(prompt, timeout_seconds=45)
    except Exception as e:
        import traceback
        print(f"CRITICAL: LLM Error (generate_single_question): {str(e)}")
        print(traceback.format_exc())
        return get_mock_single_question(field, difficulty, qtype)

def evaluate_single_answer(question: dict, user_answer: str):
    prompt = f"""
    Evaluate the user's answer for this technical question.
    
    Question: {json.dumps(question)}
    User Answer: "{user_answer}"

    Evaluation Criteria:
    - MCQs/FillBlanks: Exact match required (case-insensitive).
    - Code Snippets: Logical correctness. Full pts if works, 50% if mostly right.

    Output Format (JSON):
    {{
      "isCorrect": boolean,
      "pointsAwarded": number,
      "maxPoints": number,
      "correctAnswer": "The true correct answer",
      "feedback": "Concise feedback on why they were right/wrong"
    }}
    """

    if MOCK_AI and client is None and not GROQ_API_KEY:
        is_correct = user_answer.strip().lower() == question.get("correctAnswer", "").lower()
        return {
            "isCorrect": is_correct,
            "pointsAwarded": question.get("points") if is_correct else 0,
            "maxPoints": question.get("points"),
            "correctAnswer": question.get("correctAnswer"),
            "feedback": "Correct!" if is_correct else "Incorrect."
        }
    try:
        return _call_llm_json(prompt, timeout_seconds=30)
    except Exception as e:
        print(f"LLM Error (evaluate_single_answer): {e}")
        is_correct = user_answer.strip().lower() == question.get("correctAnswer", "").lower()
        return {
            "isCorrect": is_correct,
            "pointsAwarded": question.get("points") if is_correct else 0,
            "maxPoints": question.get("points"),
            "correctAnswer": question.get("correctAnswer"),
            "feedback": "Correct!" if is_correct else "Incorrect."
        }

def generate_full_quiz(field: str, context: dict = None):
    session_signature = str(uuid.uuid4())[:8]
    cleaned_context = _normalize_context(context)
    context_block = ''
    if cleaned_context:
        context_block = '\nCandidate Context:\n' + json.dumps(cleaned_context, ensure_ascii=False) + '\n'

    prompt = f"""
    You are a world-class technical interviewer and hiring specialist for the role: "{field}".
    Generate a short but high-quality, resume-aware assessment for this specific candidate.
    Session ID: {session_signature}
    {context_block}

    Requirements:
    1. Use the candidate's actual resume evidence: resume text, matched skills, missing skills, projects, experience, and role.
    2. At least 16 questions must assess the core responsibilities and technologies of "{field}", not generic interview trivia.
    3. Questions must test understanding of claimed skills and project decisions.
    4. Generate only two question types: "mcq" and "code_snippet".
    5. Vary the difficulty: easy, medium, and hard.
    6. Ensure different sessions generate different question sets even if the candidate context is similar.
    7. Keep each question grounded in technical reasoning the candidate would actually need for this role.
    8. Do not use data-science, business-analytics, or machine-learning topics unless they are relevant to "{field}" or the candidate's resume.
    9. Avoid repeating a question concept with only a different project or technology name.
    10. Return valid JSON only, with no markdown fences.

    JSON Schema:
    {{
      "questions": [
        {{
          "id": 1,
          "difficulty": "easy|medium|hard",
          "type": "mcq|code_snippet",
          "question": "string",
          "options": ["string", "string"],
          "correctAnswer": "string or array or object",
          "explanation": "string",
          "points": 5,
          "internal_prompt": "hidden evaluator-only prompt",
          "grading_hint": "hidden evaluator-only note"
        }}
      ]
    }}

    Constraints:
    - Generate exactly 20 questions total: a mix of MCQs (12) and short code/debugging/SQL snippet prompts (8).
    - Keep code snippets short and compact (one line or at most 8 lines).
    - Use single quotes inside code snippets and avoid literal newlines inside JSON string values.
    - Aim for roughly 6 easy, 9 medium, and 5 hard questions, grounded in the candidate's actual skills and projects.
    - Do not include any extra keys outside the schema.
    - Ensure every question has a valid `correctAnswer` and `explanation` internally for server-side grading.
    - Do not reveal the answer or explanation to the frontend; the backend will sanitize public responses.
    """

    if MOCK_AI and client is None and not GROQ_API_KEY:
        return get_mock_full_quiz(field, cleaned_context)
    try:
        parsed = _call_llm_json(prompt, timeout_seconds=75)
        validated_questions = _validate_question_set(parsed)
        if any(str(question.get("type", "")).lower() not in {"mcq", "code_snippet"} for question in validated_questions):
            for q in validated_questions:
                if str(q.get("type", "")).lower() not in {"mcq", "code_snippet"}:
                    q["type"] = "mcq" if q.get("options") and len(q.get("options")) > 1 else "code_snippet"
        return {"questions": validated_questions[:20]}
    except Exception as e:
        print(f"CRITICAL: LLM Quiz Error (generate_full_quiz): {e}")
        print("WARNING: Falling back to high-quality 20-question local quiz generator so session creation succeeds.")
        return get_mock_full_quiz(field, cleaned_context)

def evaluate_quiz_submission(questions: list, user_answers_list: list):
    total_max = len(questions) * 10
    prompt = f"""
    You are an expert technical evaluator. 
    Grade the following user answers for a {len(questions)}-question technical quiz.

    Questions Data (Reference):
    {json.dumps(questions)}

    User's Submitted Answers:
    {json.dumps(user_answers_list)}

    FAIR VALIDATION RULES:
    1. MATCHING: For each answer in 'User's Submitted Answers', find the question in 'Questions Data' with the EXACT SAME 'id'.
    2. SKIPPED: If userAnswer is "(Candidate Skipped)", ALWAYS award 0 points.
    3. MCQs: Exact option match, case-insensitive.
    4. Code snippets: accept functionally equivalent code, different formatting, quote styles, variable names, and valid alternative implementations.
    5. Grade based on technical correctness, not exact wording. Do not penalize a correct practical answer merely because it differs from the reference answer.
    6. Ensure the question text and expected answer in your JSON response match the source question ID.

    SCORING ({len(questions)} QUESTIONS, {total_max} TOTAL):
    - Every question is worth 10 points.
    - The 'maxScore' MUST be {total_max}.
    - The 'totalScore' is the sum of points awarded.
    - The 'percentage' is (totalScore / maxScore) * 100.

    Output Format (JSON):
    {{
      "totalScore": number,
      "maxScore": {total_max},
      "percentage": number,
      "results": [
        {{
          "questionId": id,
          "question": "EXACT question text from source",
          "isCorrect": boolean,
          "pointsAwarded": number (0 OR 10),
          "maxPoints": 10,
          "userAnswer": "The user's response",
          "correctAnswer": "EXACT reference answer from source",
          "feedback": "Technical justification"
        }}
      ],
      "overallFeedback": "Professional summary",
      "isSelected": boolean (True if percentage >= 70)
    }}
    """
    if MOCK_AI and client is None and not GROQ_API_KEY:
        return get_mock_evaluation_result(questions, user_answers_list)
    try:
        evaluated = _call_llm_json(prompt, timeout_seconds=75)
        results = evaluated.get("results") if isinstance(evaluated, dict) else None
        expected_ids = [str(question.get("id")) for question in questions]
        result_ids = [
            str(result.get("questionId"))
            for result in results
            if isinstance(result, dict) and result.get("questionId") is not None
        ] if isinstance(results, list) else []
        if (
            not isinstance(results, list)
            or len(results) != len(questions)
            or len(result_ids) != len(questions)
            or sorted(result_ids) != sorted(expected_ids)
        ):
            raise ValueError("AI returned an incomplete or mismatched quiz report")
        return evaluated
    except Exception as e:
        print(f"LLM Evaluation Error (evaluate_quiz_submission): {e}")
        print("Falling back to local technical quiz evaluator.")
        return get_mock_evaluation_result(questions, user_answers_list)


# --- MOCK DATA GENERATORS ---
import random


def get_mock_full_quiz(field: str, context: dict = None):
    context = context or {}
    role_specific_quiz = _get_role_specific_mock_quiz(field)
    if role_specific_quiz is not None:
        return role_specific_quiz

    skills = []
    if isinstance(context, dict):
        skills = context.get("matched_skills") or []
        skills = [str(s) for s in skills]
        if not skills:
            skills = ["Python", "SQL", "Pandas"]
    project_names = []
    if isinstance(context, dict):
        project_names = context.get("projects") or []
        project_names = [str(p) for p in project_names]
    project_focus = project_names[0] if project_names else "business analytics system"

    questions = []
    dynamic_roots = [
        f"{field}",
        "" if not skills else skills[0],
        "" if not project_names else project_names[0],
        "" if not skills else skills[-1],
    ]

    templates = [
        {
            "type": "mcq",
            "difficulty": "easy",
            "question": "Which data quality issue is most important to validate before training a model for {project}?",
            "options": ["Missing values and schema drift", "Irrelevant logging fields", "UI color contrast", "Page load speed"],
            "correctAnswer": "Missing values and schema drift",
            "explanation": "Feature quality and schema consistency directly affect train/test reliability.",
        },
        {
            "type": "sql",
            "difficulty": "easy",
            "question": "Write a SQL query to count rows where {skill1} is not null and the value is greater than 0.",
            "options": [],
            "correctAnswer": "SELECT COUNT(*) FROM table_name WHERE column_name IS NOT NULL AND column_name > 0;",
            "explanation": "This validates filtering logic and null handling in data pipelines.",
        },
        {
            "type": "output_prediction",
            "difficulty": "medium",
            "question": "Given a dataset with skewed classes, which evaluation metric would you prioritize over raw accuracy for a {field} model used in {project}?",
            "options": ["Precision-Recall tradeoff metrics such as F1 or AUC-PR", "Average row count", "Dataset file size", "Color-coded charts only"],
            "correctAnswer": "Precision-Recall tradeoff metrics such as F1 or AUC-PR",
            "explanation": "Imbalanced classes make raw accuracy misleading and require targeted metrics.",
        },
        {
            "type": "code_debugging",
            "difficulty": "medium",
            "question": "A pipeline for {project} drops rows with missing values before splitting, which is a likely bug because it can cause ____.",
            "options": ["Data leakage and biased evaluation", "Faster processing", "Color output changes", "Shorter model names"],
            "correctAnswer": "Data leakage and biased evaluation",
            "explanation": "Leakage and leakage-like issues often arise from improper preprocessing before train/test separation.",
        },
        {
            "type": "code_snippet",
            "difficulty": "hard",
            "question": "Write a Python function to compute the factorial of a non-negative integer n (iterative implementation).",
            "options": [],
            "correctAnswer": "def factorial(n):\n    result = 1\n    for i in range(2, n+1):\n        result *= i\n    return result",
            "explanation": "An iterative loop accumulates the product from 1 to n and returns the result.",
        },
        {
            "type": "mcq",
            "difficulty": "medium",
            "question": "When a resume highlights {skill1} and {skill2}, which practice best validates that those skills were used on a real project?",
            "options": ["Evaluating design tradeoffs and model metrics from the project", "Rechecking only the resume formatting", "Ignoring the project and focusing on certificates", "Counting the number of libraries mentioned"],
            "correctAnswer": "Evaluating design tradeoffs and model metrics from the project",
            "explanation": "Strong technical evidence comes from explaining decisions, systems, and measured outcomes.",
        },
        {
            "type": "dsa",
            "difficulty": "medium",
            "question": "Describe an O(n log k) approach to find the k-th largest element in an unsorted array of size n.",
            "options": [],
            "correctAnswer": "Use a min-heap of size k (or a max-heap depending on language): push items and maintain size k; final root is k-th largest. Complexity O(n log k).",
            "explanation": "Maintaining a size-k heap ensures each insertion is O(log k) and you process n elements.",
        },
        {
            "type": "output_prediction",
            "difficulty": "medium",
            "question": "Predict the output of the following Python code:\n\nprint(len([i for i in range(5) if i%2==0]))",
            "options": [],
            "correctAnswer": "3",
            "explanation": "Even numbers in range(0..4) are 0,2,4 — three items.",
        },
        {
            "type": "conceptual",
            "difficulty": "easy",
            "question": "For a {skill1}-based data workflow, why should missing-value handling be fitted using the training split rather than the full dataset?",
            "options": ["To prevent information leakage into evaluation data", "To increase the number of columns", "To avoid writing unit tests", "To change the database schema"],
            "correctAnswer": "To prevent information leakage into evaluation data",
            "explanation": "Statistics learned from the validation or test split can leak future information into training.",
        },
        {
            "type": "sql",
            "difficulty": "medium",
            "question": "In a sales table used for the {project} project, write a SQL query to return the top three regions by total revenue.",
            "options": [],
            "correctAnswer": "SELECT region, SUM(revenue) AS total_revenue FROM sales GROUP BY region ORDER BY total_revenue DESC LIMIT 3;",
            "explanation": "Grouping, aggregation, descending sort, and limiting the result identify the top regions.",
        },
        {
            "type": "code_debugging",
            "difficulty": "medium",
            "question": "A {skill1} model for {project} reports excellent test accuracy, but preprocessing was applied before the train/test split. What should be corrected?",
            "options": ["Fit preprocessing only on training data, then transform validation and test data", "Delete the test set", "Increase the UI font size", "Shuffle labels after evaluation"],
            "correctAnswer": "Fit preprocessing only on training data, then transform validation and test data",
            "explanation": "Fitting preprocessing on all rows leaks evaluation-set information and inflates the score.",
        },
        {
            "type": "scenario",
            "difficulty": "hard",
            "question": "For the {project} project, production data contains a new category not seen during training. Which approach best prevents a one-hot encoded pipeline from failing?",
            "options": ["Configure unknown categories to be ignored or mapped to a safe fallback", "Drop the entire production batch", "Fit the encoder on the production labels", "Replace the category with a random value"],
            "correctAnswer": "Configure unknown categories to be ignored or mapped to a safe fallback",
            "explanation": "Inference pipelines must handle categories that were absent from the training sample.",
        },
        {
            "type": "scenario",
            "difficulty": "medium",
            "question": "Which checks are appropriate when validating {skill1} and {skill2} code used in {project}?",
            "options": ["Input and null handling", "Boundary cases", "Measured output against an expected result", "Changing the requirements after a failure"],
            "correctAnswer": "Input and null handling",
            "explanation": "Robust technical validation covers invalid inputs, boundaries, and observable correctness.",
        },
        {
            "type": "code_snippet",
            "difficulty": "hard",
            "question": "Write a Python expression using Pandas to group a DataFrame by region and calculate total revenue for the {project} analysis.",
            "options": [],
            "correctAnswer": "df.groupby('region', as_index=False)['revenue'].sum()",
            "explanation": "GroupBy aggregates revenue independently for each region.",
        },
        {
            "type": "conceptual",
            "difficulty": "easy",
            "question": "When monitoring a {skill1} system from {project}, which signal most strongly suggests production degradation or drift?",
            "options": ["Error rates, latency percentiles, or input distributions shift materially from baseline", "The source file has a new name", "The dashboard theme changes", "The module has a shorter class name"],
            "correctAnswer": "Error rates, latency percentiles, or input distributions shift materially from baseline",
            "explanation": "Shifts in telemetry and input distributions directly indicate drift or degradation.",
        },
        {
            "type": "mcq",
            "difficulty": "medium",
            "question": "For a {field} service handling concurrent requests with {skill1}, which caching strategy prevents stale reads after writes?",
            "options": ["Write-through or explicit cache invalidation on mutation with bounded TTL", "Disabling database transactions", "Caching every POST response indefinitely", "Storing session secrets in client localStorage"],
            "correctAnswer": "Write-through or explicit cache invalidation on mutation with bounded TTL",
            "explanation": "Coordinated invalidation or write-through keeps cache state consistent with the backing store.",
        },
        {
            "type": "mcq",
            "difficulty": "hard",
            "question": "Which HTTP status code and behavior is best when an API client exceeds rate limits for a {field} endpoint?",
            "options": ["429 Too Many Requests with a Retry-After header", "500 Internal Server Error with stack trace", "200 OK with empty payload", "301 Moved Permanently"],
            "correctAnswer": "429 Too Many Requests with a Retry-After header",
            "explanation": "HTTP 429 with Retry-After signals backpressure cleanly to API consumers.",
        },
        {
            "type": "code_snippet",
            "difficulty": "medium",
            "question": "Write a function or query in {skill1} (or Python/JS) to deduplicate a list of IDs while preserving their original order.",
            "options": [],
            "correctAnswer": "list(dict.fromkeys(items))",
            "explanation": "Using dict.fromkeys or an ordered Set preserves first-seen order in O(n) time.",
        },
        {
            "type": "mcq",
            "difficulty": "easy",
            "question": "When deploying updates for {project}, why are automated integration tests executed before production rollout?",
            "options": ["To catch contract mismatches across services and databases early", "To skip code reviews completely", "To increase bundle file sizes", "To remove logging from APIs"],
            "correctAnswer": "To catch contract mismatches across services and databases early",
            "explanation": "Integration tests verify component boundaries before traffic reaches production.",
        },
        {
            "type": "mcq",
            "difficulty": "hard",
            "question": "In distributed {field} architectures, why should message consumers be idempotent?",
            "options": ["At-least-once delivery can redeliver messages, so duplicate processing must not corrupt state", "Idempotency removes the need for authentication", "It forces synchronous single-threaded execution", "It disables TLS encryption"],
            "correctAnswer": "At-least-once delivery can redeliver messages, so duplicate processing must not corrupt state",
            "explanation": "Idempotent handlers safely tolerate retries and duplicate deliveries.",
        },
    ]

    skill1 = skills[0] if skills else "Python"
    skill2 = skills[1] if len(skills) > 1 else "SQL"
    question_list = []
    for idx, template in enumerate(templates):
        q_text = template["question"].format(
            field=field,
            project=project_focus,
            skill1=skill1,
            skill2=skill2,
        )
        q = {
            "id": idx + 1,
            "difficulty": template["difficulty"],
            "type": "mcq" if template.get("options") and len(template.get("options")) > 1 else "code_snippet",
            "question": q_text,
            "options": template.get("options", []),
            "correctAnswer": template["correctAnswer"],
            "explanation": template["explanation"],
            "points": 10,
            "internal_prompt": f"Role: {field}; candidate context: {json.dumps(context, ensure_ascii=False)[:200]}",
            "grading_hint": "Use resume-aware technical reasoning and project-specific evidence.",
        }
        question_list.append(q)

    # Keep the fallback technical and personalized even when external LLMs are unavailable.
    if len(question_list) < 20:
        raise RuntimeError("The local technical question set must contain at least 20 questions")

    random.shuffle(question_list)
    return {"questions": question_list[:20]}


def _clean_text_val(val):
    if val is None:
        return ""
    if isinstance(val, (list, tuple)):
        return ", ".join(str(v).strip() for v in val if v is not None).lower()
    return str(val).strip().lower()


def get_mock_evaluation_result(questions: list, user_answers_list: list):
    # Robust programmatic grading fallback
    results = []
    total_score = 0
    max_score = 0

    for q in questions:
        q_id = q.get("id")
        user_ans = ""
        for a in user_answers_list:
            if isinstance(a, dict) and a.get("questionId") == q_id:
                user_ans = a.get("userAnswer", "")
                break

        q_corr = q.get("correctAnswer", "")
        u_clean = _clean_text_val(user_ans)
        c_clean = _clean_text_val(q_corr)

        is_correct = False
        if u_clean and u_clean != "(candidate skipped)":
            if isinstance(q_corr, (list, tuple)):
                is_correct = any(_clean_text_val(item) in u_clean or u_clean in _clean_text_val(item) for item in q_corr)
            else:
                is_correct = (u_clean == c_clean) or (len(u_clean) > 5 and u_clean in c_clean) or (len(c_clean) > 5 and c_clean in u_clean)

        pts = q.get("points", 10) if is_correct else 0
        total_score += pts
        max_score += q.get("points", 10)

        results.append({
            "questionId": q_id,
            "question": q.get("question", ""),
            "isCorrect": is_correct,
            "pointsAwarded": pts,
            "maxPoints": q.get("points", 10),
            "userAnswer": str(user_ans) if user_ans is not None else "(Candidate Skipped)",
            "correctAnswer": q_corr if isinstance(q_corr, str) else ", ".join(str(x) for x in q_corr) if isinstance(q_corr, (list, tuple)) else str(q_corr),
            "feedback": "Correct!" if is_correct else "Incorrect or alternative response."
        })

    perc = round((total_score / max_score * 100), 2) if max_score > 0 else 0
    return {
        "totalScore": total_score,
        "maxScore": max_score,
        "percentage": perc,
        "results": results,
        "overallFeedback": f"Candidate completed technical quiz evaluation with score of {perc}%.",
        "isSelected": perc >= 70
    }


def _get_role_specific_mock_quiz(field: str):
    normalized_role = re.sub(r"[^a-z0-9]", "", str(field).lower())
    security_role = any(term in normalized_role for term in ("cyber", "security", "infosec"))
    full_stack_role = any(term in normalized_role for term in ("fullstack", "webdeveloper", "frontend", "backend"))
    if not security_role and not full_stack_role:
        return None

    if security_role:
        questions = [
            ("easy", "mcq", "Which control most directly limits the damage if a service account is compromised?", ["Granting only the permissions required for its task", "Sharing one administrator account across services", "Disabling audit logs", "Using a longer session timeout"], "Granting only the permissions required for its task", "Least privilege limits the actions an exposed account can perform."),
            ("easy", "mcq", "How should an application prevent SQL injection in a login query?", ["Use parameterized queries", "Strip spaces from the submitted password", "Hide the database error page", "Encrypt the SQL query string"], "Use parameterized queries", "Bound parameters keep user input separate from executable SQL."),
            ("easy", "mcq", "What is the safest way to render untrusted text in a web page?", ["Context-aware output encoding", "Insert it directly as HTML", "Base64-encode it in the browser", "Remove only angle brackets"], "Context-aware output encoding", "Output encoding prevents untrusted input from becoming executable markup or script."),
            ("medium", "mcq", "Which second factor best resists credential phishing?", ["A hardware security key using WebAuthn", "A password reset question", "An SMS code reused for every service", "A code stored beside the password"], "A hardware security key using WebAuthn", "Origin-bound public-key authentication is resistant to common phishing proxies."),
            ("medium", "mcq", "A workstation is confirmed to be encrypting shared files. What is the first containment action?", ["Isolate the host from affected networks while preserving evidence", "Delete all security logs", "Reboot every server", "Restore backups before identifying the scope"], "Isolate the host from affected networks while preserving evidence", "Containment limits spread while preserving artifacts for investigation."),
            ("medium", "mcq", "What must a TLS client verify before trusting a server certificate?", ["The certificate chain, validity period, and hostname", "Only that the certificate file is present", "That the server uses a familiar IP address", "That the page contains a padlock icon"], "The certificate chain, validity period, and hostname", "Certificate validation establishes trust for the intended host."),
            ("medium", "mcq", "How should passwords be stored for later verification?", ["Use a password-hashing function such as Argon2id with a unique salt", "Encrypt them with a reversible key stored beside the database", "Store a fast SHA-256 hash without a salt", "Encode them with Base64"], "Use a password-hashing function such as Argon2id with a unique salt", "A salted, deliberately expensive password hash raises the cost of offline guessing."),
            ("medium", "mcq", "Why segment a network containing production servers from office workstations?", ["To constrain lateral movement after a workstation compromise", "To eliminate the need for endpoint monitoring", "To make all internal services publicly reachable", "To avoid patching internal systems"], "To constrain lateral movement after a workstation compromise", "Segmentation limits reachable assets and reduces blast radius."),
            ("medium", "mcq", "Which detail most helps correlate an authentication event across distributed services?", ["A synchronized timestamp and request or trace identifier", "The browser window size", "A user-facing display name only", "The CSS bundle version"], "A synchronized timestamp and request or trace identifier", "Consistent time and correlation identifiers connect events across systems."),
            ("hard", "mcq", "A critical vulnerability is reported on an internet-facing service. What should drive remediation priority?", ["Exploitability, exposure, asset impact, and available mitigations", "The number of characters in the advisory", "The order vulnerabilities appear in a scanner report", "Whether the service has a dark-mode interface"], "Exploitability, exposure, asset impact, and available mitigations", "Risk-based triage prioritizes likely, consequential exploitation."),
            ("hard", "mcq", "A SIEM flags a sudden spike in failed logins from one host. What is the best first step?", ["Validate the alert against source, identity, and surrounding events", "Disable all authentication logging", "Assume every event is malicious and wipe the host", "Ignore it if no user reported a problem"], "Validate the alert against source, identity, and surrounding events", "Triage should establish context and corroborating evidence before response."),
            ("hard", "mcq", "Which backup design best supports recovery from ransomware that can reach production credentials?", ["Keep tested, immutable or offline backups with separate access controls", "Mirror production files with the same administrator credentials", "Keep the only backup mounted read-write on the production server", "Back up only after an incident"], "Keep tested, immutable or offline backups with separate access controls", "Isolated, tested backups reduce the chance that ransomware destroys recovery copies."),
            ("easy", "code_snippet", "Write a Python call that queries a user by ID using a parameterized SQL statement.", [], "cursor.execute(\"SELECT * FROM users WHERE id = %s\", (user_id,))", "Binding the ID as a parameter prevents it from being interpreted as SQL."),
            ("easy", "code_snippet", "Show a Python comparison for two secret values that avoids ordinary early-exit string comparison.", [], "hmac.compare_digest(token, expected_token)", "A constant-time comparison reduces timing leakage for secret values."),
            ("medium", "code_snippet", "Write Python to generate a cryptographically secure URL-safe token with 32 bytes of entropy.", [], "secrets.token_urlsafe(32)", "The secrets module uses a cryptographically secure random source."),
            ("medium", "code_snippet", "Write a Python check that rejects a requested path if its resolved location is outside the trusted base directory.", [], "resolved_path = (base_dir / requested_path).resolve()\nif base_dir.resolve() not in resolved_path.parents and resolved_path != base_dir.resolve():\n    raise ValueError(\"Path outside allowed directory\")", "Resolve paths before checking containment to prevent traversal through .. segments."),
            ("medium", "code_snippet", "Write Python to extract IPv4-formatted addresses from a security log line.", [], "re.findall(r\"\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b\", log_line)", "Regular-expression extraction can identify address candidates for later validation and investigation."),
            ("medium", "code_snippet", "Write the Python call to hash a password using Argon2id with the argon2-cffi PasswordHasher.", [], "PasswordHasher().hash(password)", "PasswordHasher applies a salted, configurable password-hashing scheme."),
            ("hard", "code_snippet", "Write a Python expression that checks whether an IP address belongs to an allowed CIDR range.", [], "ipaddress.ip_address(client_ip) in ipaddress.ip_network(allowed_cidr)", "The ipaddress module safely parses and checks network membership."),
            ("hard", "code_snippet", "Show how to reject an HTTP request when its CSRF token does not match the session token.", [], "if not hmac.compare_digest(request_token, session_token):\n    raise PermissionError(\"Invalid CSRF token\")", "Rejecting mismatched tokens prevents the state-changing request from proceeding."),
        ]
    else:
        questions = [
            ("easy", "mcq", "In a React form, where should the current value of a controlled input come from?", ["Component state passed through value and updated by onChange", "A direct DOM query on every render", "A CSS custom property", "The browser history stack"], "Component state passed through value and updated by onChange", "Controlled inputs keep the displayed value synchronized with React state."),
            ("easy", "mcq", "Which HTTP method is normally appropriate for retrieving a resource without changing server state?", ["GET", "POST", "PATCH", "DELETE"], "GET", "GET is intended for safe resource retrieval."),
            ("easy", "mcq", "What is the safest way to store a browser session identifier for an authenticated web app?", ["An HttpOnly, Secure cookie with an appropriate SameSite policy", "A global JavaScript variable", "A URL query parameter", "A value embedded in the HTML source"], "An HttpOnly, Secure cookie with an appropriate SameSite policy", "HttpOnly limits script access and Secure restricts transmission to HTTPS."),
            ("medium", "mcq", "Why should a database transaction wrap a multi-step order update?", ["It keeps related writes atomic if a later step fails", "It makes every query run in parallel", "It prevents the API from validating input", "It removes the need for indexes"], "It keeps related writes atomic if a later step fails", "Transactions prevent partial updates across related database operations."),
            ("medium", "mcq", "Which pagination approach is generally more stable for a large, frequently changing table?", ["Keyset pagination using a stable indexed cursor", "Increasing OFFSET without limit", "Loading every row into the browser", "Sorting by a non-unique display label"], "Keyset pagination using a stable indexed cursor", "A stable cursor avoids many offset-pagination costs and shifting-page problems."),
            ("medium", "mcq", "What is the main purpose of invalidating a cache after a successful update?", ["Preventing clients from reading stale values", "Making database writes reversible", "Disabling authorization checks", "Reducing the number of application routes"], "Preventing clients from reading stale values", "Cache invalidation keeps cached reads consistent with updated data."),
            ("medium", "mcq", "What should an API return when a client sends invalid request data?", ["A suitable 4xx response with a clear validation error", "A 200 response with a fabricated success object", "A 500 response containing a stack trace", "A redirect to the database"], "A suitable 4xx response with a clear validation error", "Client input errors should be reported as client errors without leaking internals."),
            ("medium", "mcq", "How should a web application reduce cross-site scripting risk when displaying user-submitted text?", ["Escape or encode output for its HTML context", "Trust all text after a client-side check", "Store the text in a cookie", "Disable server-side validation"], "Escape or encode output for its HTML context", "Context-aware output encoding prevents text from executing as markup or script."),
            ("medium", "mcq", "What does a composite database index help with most directly?", ["Queries filtering or sorting by its indexed column sequence", "Reducing the size of every response body", "Replacing input validation", "Encrypting database backups"], "Queries filtering or sorting by its indexed column sequence", "Composite indexes support matching access patterns in their defined column order."),
            ("medium", "mcq", "What is a useful safeguard against duplicate payment requests after a network retry?", ["An idempotency key checked by the server", "A longer browser timeout", "Disabling database constraints", "A new random order ID on every retry"], "An idempotency key checked by the server", "Idempotency keys let retries return the original result without repeating the side effect."),
            ("hard", "mcq", "A React component fetches data when its search term changes. How should it avoid an older response replacing newer results?", ["Abort the previous request or ignore its result during cleanup", "Make every request synchronous", "Store results only in a module global", "Add a fixed delay after rendering"], "Abort the previous request or ignore its result during cleanup", "Cancelling or discarding stale requests avoids races between responses."),
            ("hard", "mcq", "Which test best catches a contract mismatch between an API and its database-backed service before deployment?", ["An integration test that exercises the real boundary with representative data", "A snapshot of the page's background color", "A test that only checks whether the process starts", "A manual check of variable names"], "An integration test that exercises the real boundary with representative data", "Integration tests validate behavior across component and data boundaries."),
            ("easy", "code_snippet", "Write JavaScript to remove duplicate IDs from an array while preserving their order.", [], "[...new Set(ids)]", "Set preserves insertion order and removes duplicate values."),
            ("easy", "code_snippet", "Write the React state update that increments count without relying on a stale closure.", [], "setCount(current => current + 1)", "The functional state update receives the latest state value."),
            ("medium", "code_snippet", "Write SQL to fetch the five newest orders by creation time.", [], "SELECT * FROM orders ORDER BY created_at DESC LIMIT 5;", "Ordering by creation time and limiting the result returns the newest five rows."),
            ("medium", "code_snippet", "Show a parameterized Node.js database query that selects a user by ID.", [], "db.query('SELECT * FROM users WHERE id = $1', [userId])", "A bound parameter prevents the ID from becoming executable SQL."),
            ("medium", "code_snippet", "Write JavaScript that throws an error when a fetch response is not successful.", [], "if (!response.ok) throw new Error(`Request failed: ${response.status}`)", "Checking response.ok prevents failed HTTP responses from being treated as success."),
            ("medium", "code_snippet", "Write an Express validation guard that returns HTTP 400 when req.params.id is not a positive integer.", [], "const id = Number(req.params.id);\nif (!Number.isInteger(id) || id <= 0) return res.sendStatus(400);", "Validating at the API boundary rejects malformed identifiers early."),
            ("hard", "code_snippet", "Show the SQL transaction commands needed to commit a successful operation and roll back on failure.", [], "await client.query('BEGIN');\ntry {\n  await performWrites(client);\n  await client.query('COMMIT');\n} catch (error) {\n  await client.query('ROLLBACK');\n  throw error;\n}", "A transaction commits all related writes or rolls them all back."),
            ("hard", "code_snippet", "Write JavaScript that cancels a previous fetch request using AbortController.", [], "controller.abort()", "Aborting the previous request prevents stale asynchronous work from updating the UI."),
        ]

    generated = []
    for index, (difficulty, question_type, text, options, answer, explanation) in enumerate(questions, start=1):
        generated.append({
            "id": index,
            "difficulty": difficulty,
            "type": question_type,
            "question": text,
            "options": options,
            "correctAnswer": answer,
            "explanation": explanation,
            "points": 10,
        })
    random.shuffle(generated)
    return {"questions": generated}


def get_mock_single_question(field, difficulty, qtype=None):
    points = 5 if difficulty == "easy" else 10 if difficulty == "medium" else 15
    qtype = (qtype or "mcq").lower()
    # Simple mock content for requested qtype
    base = {
        "id": random.randint(100, 999),
        "difficulty": difficulty,
        "type": qtype,
        "question": f"(MOCK) {qtype} question for {field}",
        "options": [],
        "correctAnswer": None,
        "explanation": "Automatic fallback question.",
        "points": points
    }
    if qtype == 'mcq':
        base['options'] = ["Option A", "Option B", "Option C", "Option D"]
        base['correctAnswer'] = "Option A"
        base['question'] = f"(MOCK) Which metric best indicates business impact for {field}?"
    elif qtype == 'sql':
        base['question'] = f"(MOCK) Write a SQL query for {field} to count rows where a value is greater than 0."
        base['correctAnswer'] = "SELECT COUNT(*) FROM table_name WHERE column_name > 0;"
    elif qtype == 'output_prediction' or qtype == 'code_output':
        base['question'] = f"(MOCK) Predict the output of a code snippet relevant to {field}."
        base['correctAnswer'] = "42"
    elif qtype == 'code_debugging' or qtype == 'debugging':
        base['question'] = f"(MOCK) Debug the pipeline logic in a {field} example and identify the root cause."
        base['correctAnswer'] = "The bug is an off-by-one / incorrect condition."
    elif qtype == 'scenario':
        base['question'] = f"(MOCK) Given a production scenario in {field}, what would you investigate first?"
        base['correctAnswer'] = "Check data drift and model degradation in production."
    elif qtype == 'conceptual':
        base['question'] = f"(MOCK) Explain the trade-off between precision and recall in {field}."
        base['correctAnswer'] = "Higher precision reduces false positives; higher recall reduces false negatives."
    elif qtype == 'code_snippet' or qtype == 'code_completion' or qtype == 'coding':
        base['question'] = f"(MOCK) Complete the code snippet for a technical task in {field}."
        base['correctAnswer'] = "def example(): return 42"
    elif qtype == 'dsa':
        # Small algorithm / data-structures style question for fallback mode
        base['question'] = f"(MOCK) DSA: What is the time complexity of performing a binary search on a sorted array of length n?"
        base['correctAnswer'] = "O(log n)"
    else:
        base['question'] = f"(MOCK) Generic technical question for {field}"
        base['correctAnswer'] = "Answer"
    return base
