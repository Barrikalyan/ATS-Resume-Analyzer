import sys
import re
from pathlib import Path

import pytest

import importlib

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Import modules under test
import manager
import gemini_service

TARGET_TYPES = [
    'mcq', 'sql', 'output_prediction', 'code_debugging', 'scenario',
    'conceptual', 'code_snippet', 'mcq'
]


@pytest.fixture(autouse=True)
def mock_gemini(monkeypatch):
    """Mock gemini_service AI entrypoints for deterministic tests."""

    # Always return empty full quiz so manager will call generate_single_question for each target type
    def mock_generate_full_quiz(field, context=None):
        return {"questions": []}

    def mock_generate_single_question(field, difficulty, excluded_questions, qtype=None):
        # Build a deterministic question payload matching gemini_service._normalize_question expectations
        qtype_norm = str(qtype or 'mcq').lower()
        question_text = f"{qtype_norm} question for {field}"
        options = ["A", "B", "C", "D"] if qtype_norm == 'mcq' else []
        correct = "correct-answer"
        return {
            "id": 0,
            "difficulty": difficulty,
            "type": qtype_norm,
            "question": question_text,
            "options": options,
            "correctAnswer": correct,
            "explanation": "explain",
            "points": gemini_service._difficulty_points(difficulty),
        }

    # Normalize function should be identity-like; ensure ids/points are set correctly
    def mock_normalize_question(question, index=1):
        q = dict(question)
        q.setdefault('id', index)
        q.setdefault('points', gemini_service._difficulty_points(q.get('difficulty', 'easy')))
        # Ensure options list for mcq
        if q.get('type') == 'mcq' and not isinstance(q.get('options'), list):
            q['options'] = ["A", "B"]
        return q

    # Public sanitizer should remove answer fields
    def mock_sanitize_public_question(q):
        qpub = dict(q)
        qpub['question'] = re.sub(
            r'\s*(?:\(\s*(?:variant\s+\d+|Q\s*#\s*\d+|question\s*#?\s*\d+)\s*\)|Q\s*#\s*\d+|variant\s+\d+|question\s*#?\s*\d+)\s*',
            ' ',
            str(qpub.get('question', '')),
            flags=re.IGNORECASE,
        ).strip()
        qpub.pop('correctAnswer', None)
        qpub.pop('explanation', None)
        qpub.pop('internal_prompt', None)
        qpub.pop('grading_hint', None)
        return qpub

    def mock_get_mock_single_question(field, difficulty, qtype=None):
        return mock_generate_single_question(field, difficulty, [], qtype=qtype)

    monkeypatch.setattr(gemini_service, 'generate_full_quiz', mock_generate_full_quiz)
    monkeypatch.setattr(gemini_service, 'generate_single_question', mock_generate_single_question)
    monkeypatch.setattr(gemini_service, '_normalize_question', mock_normalize_question)
    monkeypatch.setattr(gemini_service, 'sanitize_public_question', mock_sanitize_public_question)
    monkeypatch.setattr(gemini_service, 'get_mock_single_question', mock_get_mock_single_question)

    # reload manager to ensure it picks up patched gemini_service if necessary
    importlib.reload(manager)
    yield


def test_create_session_creates_twenty_questions():
    session = manager.create_session('Data Science Engineer', context={'matched_skills': ['Python'], 'projects': ['Proj']})
    assert session is not None
    assert isinstance(session.all_questions, list)
    assert len(session.all_questions) == 20
    assert len({q.get('question') for q in session.all_questions}) == 20
    # The assessment must contain a varied technical mix without requiring
    # every supported type in every generated session.
    types = [str(q.get('type', 'mcq')).lower() for q in session.all_questions]
    assert len(set(types)) >= 2


def test_all_questions_can_be_answered_and_report_generated(monkeypatch):
    session = manager.create_session('Data Science Engineer', context={'matched_skills': ['Python'], 'projects': ['Proj']})
    assert len(session.all_questions) == 20
    public_questions = [gemini_service.sanitize_public_question(q) for q in session.all_questions]
    assert all('correctAnswer' not in q for q in public_questions)
    assert all('(variant ' not in q.get('question', '').lower() for q in public_questions)
    assert all('q#' not in q.get('question', '').lower() for q in public_questions)

    # Answer each question with the known correct answer from the mocked generator
    for q in session.all_questions:
        correct = q.get('correctAnswer')
        assert correct is not None
        res = session.submit_answer(correct)
        assert res is not None
        assert res.get('status') == 'accepted'

    # After all answers, session should be finished
    assert session.is_finished is True

    monkeypatch.setattr(
        gemini_service,
        'evaluate_quiz_submission',
        lambda *args, **kwargs: pytest.fail('Final report should use the saved per-answer evaluations'),
    )
    report = session.get_final_report()
    assert 'percentage' in report
    assert 'isSelected' in report
    assert report.get('isFinished', True) is True
    assert report.get('maxScore') == 200
    assert len(report.get('results', [])) == 20
    assert report.get('percentage') == 100
    assert session.get_final_report() == report
    assert len(session.history) == 20


def test_mock_quizzes_are_role_specific_and_complete():
    full_stack = gemini_service.get_mock_full_quiz('Full Stack Developer')['questions']
    cyber_security = gemini_service.get_mock_full_quiz('Cyber Security Analyst')['questions']

    assert len(full_stack) == len(cyber_security) == 20
    assert len({question['question'] for question in full_stack}) == 20
    assert len({question['question'] for question in cyber_security}) == 20
    assert sum(question['type'] == 'mcq' for question in full_stack) == 12
    assert sum(question['type'] == 'code_snippet' for question in full_stack) == 8
    assert sum(question['type'] == 'mcq' for question in cyber_security) == 12
    assert sum(question['type'] == 'code_snippet' for question in cyber_security) == 8
    assert any('React' in question['question'] for question in full_stack)
    assert any('SIEM' in question['question'] for question in cyber_security)
    assert not any('business analytics' in question['question'].lower() for question in cyber_security)
    assert not {question['question'] for question in full_stack} & {question['question'] for question in cyber_security}


def test_incomplete_ai_quiz_report_uses_local_evaluation(monkeypatch):
    question = {
        'id': 1,
        'question': 'Which control limits an exposed service account?',
        'type': 'mcq',
        'options': ['Least privilege', 'Shared admin account'],
        'correctAnswer': 'Least privilege',
        'points': 10,
    }
    monkeypatch.setattr(gemini_service, '_call_llm_json', lambda *args, **kwargs: {'results': []})

    report = gemini_service.evaluate_quiz_submission(
        [question],
        [{'questionId': 1, 'userAnswer': 'Least privilege'}],
    )

    assert len(report['results']) == 1
    assert report['results'][0]['isCorrect'] is True
    assert report['percentage'] == 100


def test_supabase_session_persistence(monkeypatch):
    class FakeResponse:
        def __init__(self, data):
            self.data = data

    class FakeSupabase:
        def __init__(self):
            self.rows = {}
            self.operation = None

        def table(self, name):
            assert name == 'quiz_sessions'
            return self

        def upsert(self, rows, on_conflict=None):
            self.operation = 'upsert'
            self.upsert_rows = rows
            return self

        def select(self, fields):
            self.operation = 'select'
            return self

        def execute(self):
            if self.operation == 'upsert':
                for row in self.upsert_rows:
                    self.rows[row['session_id']] = row['data']
                return FakeResponse([])
            return FakeResponse([
                {'session_id': session_id, 'data': data}
                for session_id, data in self.rows.items()
            ])

    session = manager.QuizSession.__new__(manager.QuizSession)
    session.session_id = 'session-1'
    session.field = 'FullStackDeveloper'
    session.context = {'matched_skills': ['Python']}
    session.all_questions = [{'id': 1, 'question': 'Question'}]
    session.public_questions = []
    session.history = []
    session.current_index = 0
    session.max_questions = 1
    session.is_finished = False

    client = FakeSupabase()
    monkeypatch.setattr(manager, 'supabase', client)
    manager._persist_sessions(session)

    restored = manager._load_sessions()
    assert restored['session-1'].field == session.field
    assert restored['session-1'].context == session.context
    assert restored['session-1'].all_questions == session.all_questions
