import os
import ssl
import certifi
import json
import requests
from google import genai
from google.genai import types

# Fix SSL verification issue for restricted environments
os.environ["SSL_CERT_FILE"] = certifi.where()
ssl._create_default_https_context = ssl._create_unverified_context

GEMINI_FLASH_FALLBACKS = [
    "gemini-3.8-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
]

GROQ_FALLBACK_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
]


def _resolve_model_candidates(candidate=None):
    preferred = (candidate or os.environ.get("GEMINI_MODEL_ID") or "").strip()
    if preferred.startswith("models/"):
        preferred = preferred.split("/", 1)[1]
    deprecated_or_broken = {"gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-3.6-flash"}
    ordered = []
    if preferred and preferred not in deprecated_or_broken:
        ordered.append(preferred)
    for model_id in GEMINI_FLASH_FALLBACKS:
        if model_id not in ordered:
            ordered.append(model_id)
    return ordered


class GeminiAnalyzer:
    def __init__(self, api_key):
        self.timeout_seconds = max(
            5.0,
            float(os.environ.get("RESUME_AI_TIMEOUT_SECONDS", "20"))
        )
        self.api_key = api_key
        self.groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
        self.client = (
            genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000)),
            )
            if api_key
            else None
        )
        self.model_candidates = _resolve_model_candidates()
        self.model_id = self.model_candidates[0]

    def _extract_json(self, content: str):
        if not content:
            return None
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
        return None

    async def analyze_resume_semantically(self, text, role, filename, ats_context=None):
        ats_context = ats_context or {}
        matched = ats_context.get("matched_skills", [])
        inferred = ats_context.get("inferred_labels", [])
        missing = ats_context.get("missing_skills", [])
        exp_years = ats_context.get("experience_years", 0)

        prompt = f"""
You are a senior technical recruiter and ATS coach evaluating a resume for the role: {role}.

ATS Pre-Scan Context:
- Direct Matched Skills: {matched}
- Inferred Foundational Skills: {inferred}
- Missing Required Skills: {missing}
- Estimated Experience Years: {exp_years}

Resume ({filename}):
{text[:4500]}

Return ONLY valid JSON with this exact structure:
{{
  "final_score": 0-100,
  "breakdown": {{
    "skills_match": 0-100,
    "experience_relevance": 0-100,
    "education": 0-100,
    "projects": 0-100,
    "presentation": 0-100
  }},
  "dimension_feedback": {{
    "skills": "1 brief, specific sentence (max 140 chars) noting strongest match + key missing skill to add.",
    "experience": "1 brief, specific sentence (max 140 chars) identifying experience/impact flaw and how to fix it.",
    "education": "1 brief, specific sentence (max 140 chars) on degree/certification alignment or gap.",
    "projects": "1 brief, specific sentence (max 140 chars) identifying project depth/metric flaws and how to improve.",
    "ats": "1 brief, specific sentence (max 140 chars) identifying resume structure/keyword/formatting flaws."
  }},
  "strengths": ["3-4 concrete strengths citing actual resume evidence"],
  "weaknesses": ["3-4 specific resume flaws/gaps the job seeker should fix"],
  "improvements": ["3-4 actionable fixes"],
  "recommendation": "SHORTLIST|HOLD|REJECT",
  "recommendation_reasoning": "2 concise sentences explaining why.",
  "final_report": {{
    "executive_summary": "3 concise sentences",
    "category_analysis": {{
      "skills": {{ "status": "Strength|Gap", "strengths": [], "weaknesses": [], "recommendations": [] }},
      "experience": {{ "status": "Strength|Gap", "strengths": [], "weaknesses": [], "recommendations": [] }},
      "education": {{ "status": "Strength|Gap", "strengths": [], "weaknesses": [], "recommendations": [] }},
      "presentation": {{ "status": "Strength|Gap", "strengths": [], "weaknesses": [], "recommendations": [] }}
    }},
    "skills_gap_analysis": {{ "high_priority_missing": [], "medium_priority_missing": [], "learning_roadmap": [] }},
    "experience_enhancement": [],
    "project_showcase_tips": [],
    "ats_optimization_checklist": [],
    "action_plan": ["4-5 prioritized steps"]
  }}
}}

Rules:
1. Be fair and accurate: if a candidate uses React/Next.js/Vue/Angular, recognize they implicitly know HTML/CSS/JS.
2. Make every `dimension_feedback` concise, blunt, and helpful so the candidate immediately sees the flaw and how to overcome it.
3. Scoring calibration: 60-100 SHORTLIST, 40-59 HOLD, 0-39 REJECT.
"""

        if self.client is not None:
            for model_id in self.model_candidates:
                try:
                    response = self.client.models.generate_content(
                        model=model_id,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json"
                        ),
                    )
                    parsed = self._extract_json(response.text)
                    if isinstance(parsed, dict):
                        parsed["_model_used"] = model_id
                        return parsed
                except Exception as e:
                    print(f"Gemini Flash ({model_id}) resume analysis error: {e}")

        # Groq multi-model fallback if Gemini models fail
        if self.groq_api_key:
            for gm in GROQ_FALLBACK_MODELS:
                try:
                    resp = requests.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.groq_api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": gm,
                            "messages": [{"role": "user", "content": prompt}],
                            "response_format": {"type": "json_object"},
                            "temperature": 0.2,
                        },
                        timeout=self.timeout_seconds,
                    )
                    if resp.status_code == 200:
                        content = resp.json()["choices"][0]["message"]["content"]
                        parsed = self._extract_json(content)
                        if isinstance(parsed, dict):
                            parsed["_model_used"] = f"groq:{gm}"
                            return parsed
                except Exception as e:
                    print(f"Groq fallback ({gm}) resume analysis error: {e}")

        return None
