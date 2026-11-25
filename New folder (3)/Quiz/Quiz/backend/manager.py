import json
import os
import uuid
import difflib
import re
from typing import Dict, List, Optional

try:
    import gemini_service
except ModuleNotFoundError:  # support running as a package
    from . import gemini_service

import random
import threading

SESSION_STORE_PATH = os.environ.get('QUIZ_SESSION_STORE_PATH') or os.path.join(os.path.dirname(__file__), 'quiz_sessions.json')
GENERATION_TIMEOUT_SECONDS = float(os.environ.get('QUIZ_GENERATION_TIMEOUT_SECONDS', '120'))
ALLOW_LOCAL_FALLBACK = os.environ.get('QUIZ_ALLOW_LOCAL_FALLBACK', 'true').strip().lower() != 'false'
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_KEY')
supabase = None

try:
    from supabase import create_client
except ImportError:
    create_client = None

if bool(SUPABASE_URL) != bool(SUPABASE_KEY):
    raise RuntimeError('Set both SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to enable Supabase quiz persistence')
if os.environ.get('ENVIRONMENT', os.environ.get('APP_ENV', 'development')).lower() == 'production' and not (SUPABASE_URL and SUPABASE_KEY):
    raise RuntimeError('Supabase must be configured in production; local quiz-session persistence is disabled')
if SUPABASE_URL and SUPABASE_KEY:
    if SUPABASE_KEY.startswith('sb_publishable_'):
        raise RuntimeError('Use a Supabase secret/service-role key on the backend, not a publishable key')
    if create_client is None:
        raise RuntimeError('Supabase SDK is unavailable; install the Quiz backend requirements')
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def _generate_quiz_with_timeout(field: str, context: dict):
    result = {}
    failure = {}

    def generate():
        try:
            result['value'] = gemini_service.generate_full_quiz(field, context)
        except Exception as exc:
            failure['error'] = exc

    worker = threading.Thread(target=generate, daemon=True)
    worker.start()
    worker.join(GENERATION_TIMEOUT_SECONDS)
    if worker.is_alive():
        message = f"Quiz generation exceeded {GENERATION_TIMEOUT_SECONDS:g}s"
        print(f"WARNING: {message}; using the local fallback.")
        return gemini_service.get_mock_full_quiz(field, context)
    if 'error' in failure:
        print(f"WARNING: Quiz generation error ({failure['error']}); using the local fallback.")
        return gemini_service.get_mock_full_quiz(field, context)
    return result.get('value') or gemini_service.get_mock_full_quiz(field, context)


def _ensure_session_store():
    directory = os.path.dirname(SESSION_STORE_PATH) or '.'
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    if not os.path.exists(SESSION_STORE_PATH):
        with open(SESSION_STORE_PATH, 'w', encoding='utf-8') as file:
            json.dump({}, file)


def _session_data(session):
    return {
        'session_id': session.session_id,
        'field': session.field,
        'context': getattr(session, 'context', {}),
        'all_questions': session.all_questions,
        'public_questions': getattr(session, 'public_questions', []),
        'history': session.history,
        'current_index': session.current_index,
        'max_questions': session.max_questions,
        'is_finished': session.is_finished,
    }


def _persist_sessions(session=None):
    if supabase is not None:
        targets = [session] if session is not None else list(sessions.values())
        rows = [
            {'session_id': item.session_id, 'data': _session_data(item)}
            for item in targets
        ]
        if rows:
            supabase.table('quiz_sessions').upsert(rows, on_conflict='session_id').execute()
        return

    _ensure_session_store()
    payload = {
        session_id: _session_data(session)
        for session_id, session in sessions.items()
    }
    with open(SESSION_STORE_PATH, 'w', encoding='utf-8') as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def _restore_session(session_id: str, data: dict):
    session = QuizSession.__new__(QuizSession)
    session.session_id = str(data.get('session_id') or session_id)
    session.field = data.get('field') or 'General'
    session.context = data.get('context') or {}
    session.all_questions = data.get('all_questions') or []
    session.public_questions = data.get('public_questions') or []
    session.history = data.get('history') or []
    session.current_index = int(data.get('current_index', 0) or 0)
    session.max_questions = int(data.get('max_questions', len(session.all_questions)) or len(session.all_questions))
    session.is_finished = bool(data.get('is_finished'))
    return session


def _load_sessions():
    if supabase is not None:
        response = supabase.table('quiz_sessions').select('session_id,data').execute()
        raw = {
            str(row['session_id']): row['data']
            for row in response.data
            if isinstance(row.get('data'), dict)
        }
    else:
        _ensure_session_store()
        if not os.path.exists(SESSION_STORE_PATH):
            return {}
        try:
            with open(SESSION_STORE_PATH, 'r', encoding='utf-8') as file:
                raw = json.load(file)
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    restored = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        restored[str(key)] = _restore_session(str(key), value)
    return restored

class QuizSession:
    def __init__(self, field: str, context: dict = None):
        self.session_id = str(uuid.uuid4())
        self.field = field
        self.context = context or {}

        self.all_questions = []
        max_retries = 3
        last_questions = []
        for attempt in range(max_retries):
            try:
                full_quiz = _generate_quiz_with_timeout(field, self.context)
            except Exception as exc:
                print(f"WARNING: Quiz generation failed: {exc}; using the local fallback.")
                full_quiz = gemini_service.get_mock_full_quiz(field, self.context)
            questions = full_quiz.get("questions", [])
            if len(questions) >= 6 and len(set(q.get("question", "") for q in questions)) >= len(questions) * 0.8:
                self.all_questions = questions
                break
            last_questions = questions
            print(f"DEBUG: Quiz generation attempt {attempt + 1} failed diversity check. Retrying...")

        if not self.all_questions:
            self.all_questions = last_questions if last_questions else []

        # Enforce a 20-question MCQ/code-snippet Round 2.
        desired_count = 20
        target_types = (
            ['mcq'] * 12 + ['code_snippet'] * 8
        )
        # Normalize all question types to mcq or code_snippet
        for q in self.all_questions:
            if str(q.get('type', 'mcq')).lower() not in {'mcq', 'code_snippet'}:
                q['type'] = 'mcq' if q.get('options') and len(q.get('options')) > 1 else 'code_snippet'

        if len(self.all_questions) >= desired_count:
            # A complete AI or local fallback set already has enough questions.
            # Reuse it instead of making extra network calls to rebalance types.
            target_types = [str(q.get('type', 'mcq')).lower() for q in self.all_questions[:desired_count]]

        # Build lookup of existing questions by type
        existing_by_type = {}
        for q in list(self.all_questions):
            t = str(q.get('type', 'mcq')).lower()
            existing_by_type.setdefault(t, []).append(q)

        local_fallback_questions = gemini_service.get_mock_full_quiz(field, self.context).get('questions', [])
        fallback_by_type = {}
        for question in local_fallback_questions:
            question_type = str(question.get('type', 'mcq')).lower()
            fallback_by_type.setdefault(question_type, []).append(question)

        selected = []
        excluded_texts = [q.get('question') for q in self.all_questions if isinstance(q.get('question'), str)]
        seen_questions = set()

        for desired in target_types:
            # If we have an existing question of this type, prefer using it
            bucket = existing_by_type.get(desired, [])
            chosen = None
            for candidate in bucket:
                question_text = candidate.get('question')
                if isinstance(question_text, str) and question_text in seen_questions:
                    continue
                chosen = candidate
                break
            if chosen is not None:
                selected.append(chosen)
                if isinstance(chosen.get('question'), str):
                    seen_questions.add(chosen.get('question'))
                    excluded_texts.append(chosen.get('question'))
                continue

            for candidate in fallback_by_type.get(desired, []):
                question_text = candidate.get('question')
                if isinstance(question_text, str) and question_text in seen_questions:
                    continue
                chosen = candidate
                break
            if chosen is not None:
                selected.append(chosen)
                if isinstance(chosen.get('question'), str):
                    seen_questions.add(chosen.get('question'))
                    excluded_texts.append(chosen.get('question'))
                continue

            # Otherwise, attempt to generate a single question of the desired type
            try:
                q = gemini_service.generate_single_question(field, random.choice(['easy', 'medium', 'hard']), excluded_texts, qtype=desired)
                try:
                    normalized = gemini_service._normalize_question(q, len(selected) + 1)
                except Exception:
                    q['id'] = len(selected) + 1
                    if 'points' not in q:
                        q['points'] = gemini_service._difficulty_points(q.get('difficulty', 'easy'))
                    normalized = q
                question_text = normalized.get('question')
                if isinstance(question_text, str) and question_text in seen_questions:
                    raise ValueError('duplicate question generated')
                if question_text:
                    excluded_texts.append(question_text)
                    seen_questions.add(question_text)
                selected.append(normalized)
            except Exception:
                mq = gemini_service.get_mock_single_question(field, random.choice(['easy', 'medium', 'hard']), desired)
                mq['id'] = len(selected) + 1
                question_text = mq.get('question')
                if isinstance(question_text, str) and question_text in seen_questions:
                    mq['question'] = f"{question_text} (variant {len(selected) + 1})"
                if isinstance(mq.get('question'), str):
                    seen_questions.add(mq.get('question'))
                selected.append(mq)

        # If there were more existing questions of other types, fill any remaining slots (shouldn't happen)
        leftovers = []
        for lst in existing_by_type.values():
            leftovers.extend(lst)
        while len(selected) < desired_count and leftovers:
            candidate = leftovers.pop(0)
            question_text = candidate.get('question')
            if isinstance(question_text, str) and question_text in seen_questions:
                continue
            if isinstance(question_text, str):
                seen_questions.add(question_text)
            selected.append(candidate)

        # Final safety: if still fewer than desired_count, pad with unique mock questions
        while len(selected) < desired_count:
            mq = gemini_service.get_mock_single_question(field, random.choice(['easy','medium','hard']))
            mq['id'] = len(selected) + 1
            question_text = mq.get('question')
            if isinstance(question_text, str) and question_text in seen_questions:
                mq['question'] = f"{question_text} (variant {len(selected) + 1})"
            if isinstance(mq.get('question'), str):
                seen_questions.add(mq.get('question'))
            selected.append(mq)

        self.all_questions = selected
        # Normalize points so total maxScore is consistently 200 (20 questions * 10 pts)
        for q in self.all_questions:
            try:
                q['points'] = 10
            except Exception:
                q.update({'points': 10})
        random.shuffle(self.all_questions)
        self.public_questions = [gemini_service.sanitize_public_question(q) for q in self.all_questions]

        self.history = []
        self.max_questions = len(self.all_questions) if self.all_questions else 0
        self.is_finished = False
        self.current_index = 0

    def _normalize_answer(self, value):
        if value is None:
            return ""
        if isinstance(value, list):
            return "|".join(str(v).strip().lower() for v in value)
        if isinstance(value, dict):
            return json.dumps(value, sort_keys=True).lower()
        return str(value).strip().lower()

    def _subjective_answer_matches(self, user_answer, correct_answer, qtype=""):
        """Accept equivalent code, SQL, and technical explanations without weakening MCQs."""
        user_text = self._normalize_answer(user_answer)
        correct_text = self._normalize_answer(correct_answer)
        if user_text == correct_text:
            return True

        def tokens(value):
            return set(re.findall(r"[a-z0-9_+#.-]+", value.lower()))

        user_tokens = tokens(user_text)
        correct_tokens = tokens(correct_text)
        if not user_tokens or not correct_tokens:
            return False

        shared = len(user_tokens & correct_tokens)
        coverage = shared / len(correct_tokens)
        relevance = shared / len(user_tokens)

        if qtype in {"code_snippet", "code_debugging", "sql", "output_prediction"}:
            compact_user = re.sub(r"[^a-z0-9]", "", user_text)
            compact_correct = re.sub(r"[^a-z0-9]", "", correct_text)
            if difflib.SequenceMatcher(None, compact_user, compact_correct).ratio() >= 0.55:
                return True
            return coverage >= 0.45 and relevance >= 0.25

        if qtype == "dsa":
            if "heap" in user_tokens and ("complexity" in user_tokens or "log" in user_tokens) and ("size" in user_tokens or "k" in user_tokens):
                return True
            return coverage >= 0.4 and relevance >= 0.2

        # Short factual answers need to contain nearly all key terms.
        if len(correct_tokens) <= 8:
            return coverage >= 0.75 and relevance >= 0.35

        # Longer explanations and implementations can use different wording,
        # but must cover most of the reference's technical concepts.
        return coverage >= 0.55 and relevance >= 0.18

    def _score_question(self, question: dict, user_answer: str):
        if user_answer is None or str(user_answer).strip() == "" or str(user_answer).strip() == "(Candidate Skipped)":
            return {"isCorrect": False, "pointsAwarded": 0, "maxPoints": question.get("points", 0), "feedback": "No answer submitted."}

        correct_answer = question.get("correctAnswer")
        if correct_answer is None:
            return {"isCorrect": False, "pointsAwarded": 0, "maxPoints": question.get("points", 0), "feedback": "No correct answer available for this question."}

        qtype = str(question.get("type", "mcq")).lower()
        if qtype == "mcq":
            normalized_user = self._normalize_answer(user_answer)
            normalized_correct = self._normalize_answer(correct_answer)
            is_correct = normalized_user == normalized_correct
            if not is_correct and isinstance(correct_answer, list):
                is_correct = set(normalized_user.split("|")) == {self._normalize_answer(item) for item in correct_answer}
            points = question.get("points", 0) if is_correct else 0
            feedback = "Correct." if is_correct else "Not fully correct. Review the technical concept and answer again."
            return {"isCorrect": is_correct, "pointsAwarded": points, "maxPoints": question.get("points", 0), "feedback": feedback}

        if qtype in {"fill_blank", "code_snippet", "code_debugging", "output_prediction", "sql", "scenario", "conceptual", "dsa", "multiple_select"}:
            if isinstance(correct_answer, list):
                user_items = {self._normalize_answer(item) for item in (user_answer if isinstance(user_answer, list) else [user_answer])}
                correct_items = {self._normalize_answer(item) for item in correct_answer}
                is_correct = user_items == correct_items
            else:
                is_correct = self._subjective_answer_matches(user_answer, correct_answer, qtype)
            points = question.get("points", 0) if is_correct else 0
            feedback = "Correct." if is_correct else "Not fully correct. Review the technical concept and answer again."
            return {"isCorrect": is_correct, "pointsAwarded": points, "maxPoints": question.get("points", 0), "feedback": feedback}

        is_correct = False
        points = 0
        return {"isCorrect": is_correct, "pointsAwarded": points, "maxPoints": question.get("points", 0), "feedback": "This question requires manual evaluation."}

    def generate_next_question(self):
        if self.current_index >= self.max_questions:
            self.is_finished = True
            return None

        q = self.all_questions[self.current_index]
        return gemini_service.sanitize_public_question(q)

    def submit_answer(self, user_answer: str):
        if self.is_finished or self.current_index >= self.max_questions:
            return None

        question = self.all_questions[self.current_index]
        evaluation = self._score_question(question, user_answer)
        self.history.append({
            "question": question,
            "userAnswer": user_answer,
            "evaluation": evaluation,
        })

        self.current_index += 1
        if self.current_index >= self.max_questions:
            self.is_finished = True

        _persist_sessions(self)
        return {
            "status": "accepted",
            "isCorrect": evaluation["isCorrect"],
            "pointsAwarded": evaluation["pointsAwarded"],
            "maxPoints": evaluation["maxPoints"],
            "feedback": evaluation["feedback"],
        }

    def get_final_report(self):
        results = []
        total_score = 0
        max_score = 0
        for record in self.history:
            question = record["question"]
            evaluation = record.get("evaluation") or {}
            question_max = int(evaluation.get("maxPoints") or question.get("points", 10) or 10)
            question_score = min(max(int(evaluation.get("pointsAwarded", 0) or 0), 0), question_max)
            total_score += question_score
            max_score += question_max
            results.append({
                "questionId": question.get("id"),
                "question": question.get("question"),
                "isCorrect": evaluation.get("isCorrect", False),
                "pointsAwarded": question_score,
                "maxPoints": question_max,
                "userAnswer": record.get("userAnswer"),
                "correctAnswer": question.get("correctAnswer"),
                "feedback": evaluation.get("feedback", ""),
            })

        percentage = round((total_score / max_score * 100), 2) if max_score else 0
        return {
            "totalScore": total_score,
            "maxScore": max_score,
            "percentage": percentage,
            "results": results,
            "overallFeedback": "Technical assessment completed. The backend evaluated your responses and compared them against the role-specific answer key.",
            "isSelected": percentage >= 70,
            "isFinished": True,
        }


sessions: Dict[str, QuizSession] = _load_sessions()

def create_session(field: str, context: dict = None) -> QuizSession:
    session = QuizSession(field, context=context)
    sessions[session.session_id] = session
    _persist_sessions(session)
    return session

def get_session(session_id: str) -> Optional[QuizSession]:
    session = sessions.get(session_id)
    if session is not None:
        return session

    restored = _load_sessions().get(session_id)
    if restored is not None:
        sessions[session_id] = restored
        return restored
    return None
