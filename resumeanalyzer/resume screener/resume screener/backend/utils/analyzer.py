import re
import math
import asyncio
from datetime import datetime
from backend.config import JOB_ROLES, SKILL_ALIASES, IMPLIED_SKILLS
from backend.utils.ai_analyzer import GeminiAnalyzer


class StructuralExtractor:
    def __init__(self, text, filename, role_skills):
        self.text = text or ""
        self.filename = filename or ""
        self.text_lower = self.text.lower()
        self.role_skills = role_skills or []

    def extract(self):
        lines = self.text.split("\n")
        useful_lines = [l.strip() for l in lines if l.strip()]
        skill_data = self._extract_skills()

        return {
            "skills": skill_data["all_skills"],
            "direct_skills": skill_data["direct_skills"],
            "inferred_skills": skill_data["inferred_skills"],
            "experience": self._extract_experience(useful_lines),
            "education": self._extract_education(),
            "certifications": self._extract_certifications(useful_lines),
            "projects": self._extract_projects(useful_lines),
            "ats_quality": self._extract_ats_quality(useful_lines),
        }

    def _matches_term(self, term: str) -> bool:
        escaped = re.escape(term)
        pattern = rf"(?i)(?<!\w){escaped}(?!\w)"
        return bool(re.search(pattern, self.text))

    def _extract_skills(self):
        search_space = set(self.role_skills)
        search_space.update(SKILL_ALIASES.keys())
        search_space.update(IMPLIED_SKILLS.keys())
        search_space.update([
            "AWS", "Azure", "GCP", "Docker", "Kubernetes", "C++", "Java", "Python",
            "SQL", "Git", "GitHub", "React", "Next.js", "Angular", "Vue.js",
            "Node.js", "Express.js", "MongoDB", "PostgreSQL", "MySQL", "Redis",
            "JavaScript", "HTML", "CSS", "TypeScript", "Linux", "Shell Scripting",
            "Agile", "Scrum", "Django", "Flask", "FastAPI", "Spring", "Spring Boot",
            "Core Java", "Hibernate", "Tailwind CSS", "CI/CD", "Terraform",
            "Playwright", "Cypress", "Selenium", "Flutter", "React Native",
        ])

        direct_skills = []
        for canonical in sorted(search_space):
            terms = [canonical] + SKILL_ALIASES.get(canonical, [])
            if any(self._matches_term(t) for t in terms):
                direct_skills.append(canonical)

        direct_set = set(direct_skills)
        inferred_map = {}
        # Infer foundational competencies from demonstrated frameworks/platforms
        for source_skill in list(direct_skills):
            for implied in IMPLIED_SKILLS.get(source_skill, []):
                if implied not in direct_set and implied not in inferred_map:
                    inferred_map[implied] = source_skill

        inferred_skills = [
            {"skill": skill, "via": via}
            for skill, via in sorted(inferred_map.items())
        ]
        all_skills = sorted(direct_set | set(inferred_map.keys()))

        return {
            "direct_skills": sorted(direct_set),
            "inferred_skills": inferred_skills,
            "all_skills": all_skills,
        }

    def _extract_experience(self, lines):
        current_year = datetime.now().year
        total_years = 0.0

        # 1. Explicit "X years of experience" mentions
        explicit_matches = re.findall(
            r"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)\s*(?:of\s*)?(?:experience|exp)?",
            self.text_lower,
        )
        if explicit_matches:
            total_years = max(float(m) for m in explicit_matches)

        # 2. Date ranges inside experience-like lines (e.g., 2021 - 2024 or 2023 - Present)
        range_matches = re.findall(
            r"\b((?:19|20)\d{2})\s*[-–to]+\s*((?:19|20)\d{2}|present|current|now)\b",
            self.text_lower,
        )
        range_years = 0.0
        for start_s, end_s in range_matches:
            start_y = int(start_s)
            end_y = current_year if end_s in {"present", "current", "now"} else int(end_s)
            if 1990 <= start_y <= current_year and end_y >= start_y:
                diff = end_y - start_y
                if diff <= 25:
                    range_years = max(range_years, float(diff))

        total_years = round(max(total_years, range_years), 1)

        # Detect internship / role lines
        titles_found = []
        known_titles = [
            "developer", "engineer", "analyst", "manager", "intern",
            "architect", "consultant", "scientist", "specialist", "lead", "sdet",
        ]
        for line in lines[:45]:
            l_lower = line.lower()
            if any(t in l_lower for t in known_titles) and 4 <= len(line) < 70:
                titles_found.append({"title": line, "skills_used": [], "duration_months": int(max(6, total_years * 12))})

        has_internship = "intern" in self.text_lower
        if total_years == 0 and has_internship:
            total_years = 0.5

        if not titles_found:
            titles_found.append({"title": "Candidate", "skills_used": [], "duration_months": int(total_years * 12)})

        return {
            "total_years": max(0, total_years),
            "roles": titles_found[:5],
            "has_internship": has_internship,
        }

    def _extract_education(self):
        deg = "Unknown"
        if any(k in self.text_lower for k in ["ph.d", "phd", "doctorate"]):
            deg = "PhD"
        elif any(k in self.text_lower for k in ["m.tech", "m.e", "m.sc", "mca", "master", "mba", "ms in"]):
            deg = "Master's / PG"
        elif any(k in self.text_lower for k in ["b.tech", "b.e", "b.sc", "bca", "bachelor", "btech"]):
            deg = "B.Tech / Bachelor's"

        branch = "Unknown"
        if any(k in self.text_lower for k in ["computer science", "cse", "cs&e", "software engineering"]):
            branch = "CSE"
        elif any(k in self.text_lower for k in ["information technology", "information science"]):
            branch = "IT"
        elif any(k in self.text_lower for k in ["artificial intelligence", "data science", "machine learning"]):
            branch = "AI & DS"
        elif any(k in self.text_lower for k in ["electronics", "ece", "electrical"]):
            branch = "ECE"

        return {"degree": deg, "branch": branch}

    def _extract_certifications(self, lines):
        return [l for l in lines if any(k in l.lower() for k in ["certif", "aws certified", "coursera", "udemy", "credential"]) and len(l) < 100][:5]

    def _extract_projects(self, lines):
        projects = []
        in_projects = False
        section_headers = {"experience", "education", "skills", "certifications", "summary", "achievements", "publications"}
        action_verbs = ["developed", "implemented", "deployed", "optimized", "automated", "built", "architected", "designed", "engineered", "created"]

        project_lines = []
        for line in lines:
            low = line.lower().strip(": ")
            if "project" in low and len(line) < 40:
                in_projects = True
                continue
            if in_projects and any(h == low or low.startswith(h + " ") for h in section_headers) and len(line) < 35:
                in_projects = False
            elif in_projects:
                project_lines.append(line)

        has_metrics = bool(re.search(r"\d+%|\d+x|\$\d+|\d+\s*(users|requests|ms|sec|records|fps)", self.text_lower))
        has_links = any(k in self.text_lower for k in ["github.com", "gitlab.com", "vercel.app", "netlify.app", "herokuapp", "http://", "https://"])
        verbs_found = [v for v in action_verbs if v in self.text_lower]

        if project_lines or "project" in self.text_lower:
            joined = " ".join(project_lines) if project_lines else self.text[:1500]
            projects.append({
                "title": project_lines[0][:60] if project_lines else "Technical Projects",
                "tools": verbs_found[:4],
                "description": joined[:800],
                "has_metrics": has_metrics,
                "has_links": has_links,
                "line_count": len(project_lines),
            })
        return projects

    def _extract_ats_quality(self, lines):
        word_count = len(self.text.split())
        sections = sum(1 for s in ["skill", "education", "experience", "project"] if s in self.text_lower)
        has_email = bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", self.text))
        has_links = any(k in self.text_lower for k in ["github", "linkedin", "http"])
        return {
            "readable": word_count >= 80,
            "word_count": word_count,
            "sections_detected": sections >= 3,
            "section_count": sections,
            "has_contact": has_email,
            "has_links": has_links,
            "format": "PDF" if self.filename.lower().endswith(".pdf") else ("DOCX" if self.filename.lower().endswith(".docx") else "TXT"),
        }


class DepthAnalyzer:
    def __init__(self, ai_api_key=None):
        self.job_roles = JOB_ROLES
        self.ai_analyzer = GeminiAnalyzer(ai_api_key) if ai_api_key else None

    async def analyze(self, text, raw_role, filename):
        target_role = raw_role
        job_config = self.job_roles.get(target_role, {})
        req_skills = job_config.get("skills_required", [])

        job = {
            "required_skills": req_skills,
            "preferred_skills": [],
            "min_experience": job_config.get("min_experience", 2),
            "job_domain": job_config.get("domain", "AI" if "AI" in target_role or "Data" in target_role else "Web"),
            "education_required": {"degree": "B.Tech / Bachelor's", "branch": ["CSE", "IT", "AI & DS"]},
        }

        extractor = StructuralExtractor(text, filename, req_skills)
        resume = await asyncio.to_thread(extractor.extract)

        req_set = set(req_skills)
        direct_matched = [s for s in req_skills if s in set(resume["direct_skills"])]
        inferred_matched = [item for item in resume["inferred_skills"] if item["skill"] in req_set]
        all_matched_set = set(direct_matched) | {item["skill"] for item in inferred_matched}
        matched_skills = [s for s in req_skills if s in all_matched_set]
        missing_skills = [s for s in req_skills if s not in all_matched_set]

        # Python ATS Engine deterministic baseline
        rules_result = self.calculate_final_score(resume, job, matched_skills, missing_skills)
        rule_feedback = self.generate_feedback(rules_result["parameter_scores"], resume, job, matched_skills, inferred_matched, missing_skills)

        ai_result = None
        if self.ai_analyzer:
            ats_context = {
                "matched_skills": matched_skills,
                "inferred_labels": [f"{i['skill']} (via {i['via']})" for i in inferred_matched],
                "missing_skills": missing_skills,
                "experience_years": resume["experience"]["total_years"],
            }
            try:
                ai_result = await asyncio.wait_for(
                    self.ai_analyzer.analyze_resume_semantically(text, target_role, filename, ats_context=ats_context),
                    timeout=self.ai_analyzer.timeout_seconds + 2,
                )
            except asyncio.TimeoutError:
                print(f"DEBUG: Gemini Flash analysis timed out for '{filename}'")
            except Exception as e:
                print(f"DEBUG: Gemini Flash analysis failed for '{filename}': {e}")

        if ai_result and isinstance(ai_result, dict):
            ai_breakdown = ai_result.get("breakdown", {}) or {}
            # Blend Python ATS Engine (45%) + Gemini Flash semantic evaluation (55%)
            blended_breakdown = {
                "skills": round(rules_result["breakdown"]["skills"] * 0.45 + float(ai_breakdown.get("skills_match", rules_result["breakdown"]["skills"])) * 0.55, 1),
                "experience": round(rules_result["breakdown"]["experience"] * 0.45 + float(ai_breakdown.get("experience_relevance", rules_result["breakdown"]["experience"])) * 0.55, 1),
                "education": round(rules_result["breakdown"]["education"] * 0.45 + float(ai_breakdown.get("education", rules_result["breakdown"]["education"])) * 0.55, 1),
                "projects": round(rules_result["breakdown"]["projects"] * 0.45 + float(ai_breakdown.get("projects", rules_result["breakdown"]["projects"])) * 0.55, 1),
                "format": round(rules_result["breakdown"]["format"] * 0.45 + float(ai_breakdown.get("presentation", rules_result["breakdown"]["format"])) * 0.55, 1),
            }
            ai_final = float(ai_result.get("final_score", rules_result["final_score"]))
            final_score = round(rules_result["final_score"] * 0.40 + ai_final * 0.60, 2)

            dim_fb = ai_result.get("dimension_feedback") or {}
            merged_feedback = {
                "skills": dim_fb.get("skills") or rule_feedback["skills"],
                "experience": dim_fb.get("experience") or rule_feedback["experience"],
                "education": dim_fb.get("education") or rule_feedback["education"],
                "projects": dim_fb.get("projects") or rule_feedback["projects"],
                "ats": dim_fb.get("ats") or rule_feedback["ats"],
            }

            return {
                "parameter_scores": blended_breakdown,
                "final_score": final_score,
                "decision": "Shortlist" if final_score >= 60 else "Hold" if final_score >= 40 else "Reject",
                "breakdown": blended_breakdown,
                "feedback": merged_feedback,
                "ai_analysis": ai_result,
                "recommendation": "SHORTLIST" if final_score >= 60 else ("HOLD" if final_score >= 40 else "REJECT"),
                "recommendation_reasoning": ai_result.get("recommendation_reasoning", "Combined Python ATS + Gemini Flash evaluation complete."),
                "final_report": ai_result.get("final_report", {}),
                "role": target_role,
                "matched_skills": matched_skills,
                "direct_matched_skills": direct_matched,
                "inferred_skills": inferred_matched,
                "missing_skills": missing_skills,
                "evaluation_engine": f"Python ATS + {ai_result.get('_model_used', 'Gemini Flash')}",
            }

        # Fallback to pure Python ATS Engine
        fallback_report = {
            "executive_summary": f"Python ATS Engine completed evaluation with {rules_result['final_score']}% alignment for {target_role}.",
            "skills_gap_analysis": {
                "high_priority_missing": missing_skills[:3],
                "medium_priority_missing": missing_skills[3:6],
                "learning_roadmap": [f"Add hands-on project or certification for {s}." for s in missing_skills[:3]],
            },
            "action_plan": [
                rule_feedback["skills"],
                rule_feedback["experience"],
                rule_feedback["projects"],
                rule_feedback["ats"],
            ],
        }

        return {
            "parameter_scores": rules_result["parameter_scores"],
            "final_score": rules_result["final_score"],
            "decision": rules_result["decision"],
            "breakdown": rules_result["breakdown"],
            "feedback": rule_feedback,
            "ai_analysis": {
                "strengths": [
                    f"Matched {len(matched_skills)}/{len(req_skills)} target role skills ({', '.join(matched_skills[:5])})." if matched_skills else "Resume parsed cleanly.",
                    f"ATS readability check passed ({resume['ats_quality']['word_count']} words).",
                ],
                "weaknesses": [
                    f"Missing required skills: {', '.join(missing_skills[:5])}." if missing_skills else "No major skill gaps detected.",
                    rule_feedback["experience"],
                    rule_feedback["projects"],
                ],
                "recommendation": rules_result["decision"].upper(),
                "recommendation_reasoning": f"Python ATS scored {rules_result['final_score']}% based on {len(matched_skills)}/{len(req_skills)} skills and {resume['experience']['total_years']}y experience.",
            },
            "recommendation": rules_result["decision"].upper(),
            "recommendation_reasoning": f"Python ATS Engine scored {rules_result['final_score']}% based on {len(matched_skills)}/{len(req_skills)} required skills.",
            "final_report": fallback_report,
            "role": target_role,
            "matched_skills": matched_skills,
            "direct_matched_skills": direct_matched,
            "inferred_skills": inferred_matched,
            "missing_skills": missing_skills,
            "evaluation_engine": "Python ATS Engine",
        }

    # ==============================
    # TRANSPARENT PYTHON ATS SCORING
    # ==============================

    def score_skills(self, resume, job, matched_skills=None):
        req = job["required_skills"]
        if not req:
            return 0
        if matched_skills is None:
            matched_skills = list(set(resume["skills"]) & set(req))
        coverage = len(matched_skills) / len(req)
        # Smooth curve so matching 70%+ of core skills yields a strong score
        score = (0.75 * coverage + 0.25 * math.sqrt(coverage)) * 100
        return round(min(100.0, score), 2)

    def score_experience(self, resume, job):
        years = resume["experience"]["total_years"]
        min_exp = max(1, job["min_experience"])
        years_ratio = min(years / min_exp, 1.0)
        titles = [r["title"].lower() for r in resume["experience"]["roles"]]
        role_match = 1.0 if any(k in t for t in titles for k in ["engineer", "developer", "analyst", "scientist", "architect", "intern"]) else 0.5
        has_intern = 0.15 if resume["experience"].get("has_internship") and years < 1 else 0.0
        score = (years_ratio * 0.65 + role_match * 0.35 + has_intern) * 100
        return round(min(100.0, max(score, 35.0)), 2)

    def score_education(self, resume, job):
        edu = resume["education"]
        degree_score = 1.0 if edu["degree"] in {"B.Tech / Bachelor's", "Master's / PG", "PhD"} else 0.55
        branch_score = 1.0 if edu["branch"] != "Unknown" else 0.65
        cert_score = min(len(resume["certifications"]) / 2, 1.0)
        score = (degree_score * 0.50 + branch_score * 0.30 + cert_score * 0.20) * 100
        return round(score, 2)

    def score_projects(self, resume, job):
        if not resume["projects"]:
            return 30.0
        proj = resume["projects"][0]
        verb_score = min(len(proj.get("tools", [])) / 3, 1.0)
        metrics_score = 1.0 if proj.get("has_metrics") else 0.35
        links_score = 1.0 if proj.get("has_links") else 0.40
        depth_score = 1.0 if proj.get("line_count", 0) >= 4 else 0.60
        score = (verb_score * 0.30 + metrics_score * 0.30 + links_score * 0.20 + depth_score * 0.20) * 100
        return round(score, 2)

    def score_ats(self, resume):
        ats = resume["ats_quality"]
        readability = 1.0 if ats["readable"] else 0.5
        sections = min(ats.get("section_count", 2) / 4, 1.0)
        contact = 1.0 if ats.get("has_contact") else 0.6
        links = 1.0 if ats.get("has_links") else 0.7
        score = (readability * 0.35 + sections * 0.35 + contact * 0.15 + links * 0.15) * 100
        return round(score, 2)

    def calculate_final_score(self, resume, job, matched_skills=None, missing_skills=None):
        scores = {
            "skills": self.score_skills(resume, job, matched_skills),
            "experience": self.score_experience(resume, job),
            "education": self.score_education(resume, job),
            "projects": self.score_projects(resume, job),
            "ats": self.score_ats(resume),
        }

        final = (
            scores["skills"] * 0.40
            + scores["experience"] * 0.25
            + scores["education"] * 0.15
            + scores["projects"] * 0.10
            + scores["ats"] * 0.10
        )

        return {
            "parameter_scores": scores,
            "final_score": round(final, 2),
            "decision": "Shortlist" if final >= 60 else "Hold" if final >= 40 else "Reject",
            "breakdown": {
                "skills": scores["skills"],
                "experience": scores["experience"],
                "education": scores["education"],
                "projects": scores["projects"],
                "format": scores["ats"],
            },
        }

    def generate_feedback(self, scores, resume, job, matched_skills, inferred_matched, missing_skills):
        feedback = {}
        req_count = len(job["required_skills"])

        inferred_note = f" (incl. {', '.join(i['skill'] + ' via ' + i['via'] for i in inferred_matched[:2])})" if inferred_matched else ""
        if missing_skills:
            feedback["skills"] = f"Matched {len(matched_skills)}/{req_count} role skills{inferred_note}. Add missing: {', '.join(missing_skills[:3])}."
        else:
            feedback["skills"] = f"Strong match: all {req_count} target role skills covered{inferred_note}."

        years = resume["experience"]["total_years"]
        min_exp = job["min_experience"]
        if years < min_exp:
            feedback["experience"] = f"Detected {years}y vs {min_exp}y target. Add dates, internships, and quantified impact bullets."
        else:
            feedback["experience"] = f"Detected {years}y experience; sharpen bullets with measurable outcomes (%, latency, scale)."

        deg = resume["education"]["degree"]
        certs = len(resume["certifications"])
        if deg == "Unknown" or certs == 0:
            feedback["education"] = f"Degree: {deg}. List degree/branch clearly and add 1-2 role-relevant certifications."
        else:
            feedback["education"] = f"Degree ({deg}) and {certs} certification(s) align well with role expectations."

        if not resume["projects"]:
            feedback["projects"] = "Flaw: No dedicated Projects section found. Add 2-3 projects with tech stack, metrics, and GitHub links."
        else:
            proj = resume["projects"][0]
            flaws = []
            if not proj.get("has_metrics"):
                flaws.append("quantified results (%, users, speed)")
            if not proj.get("has_links"):
                flaws.append("GitHub/live demo links")
            if flaws:
                feedback["projects"] = f"Projects detected, but missing {' and '.join(flaws)} — add them to boost rigor."
            else:
                feedback["projects"] = "Projects include strong action verbs, measurable metrics, and repository/live links."

        ats = resume["ats_quality"]
        ats_flaws = []
        if not ats.get("sections_detected"):
            ats_flaws.append("standard headings (Skills, Experience, Projects, Education)")
        if not ats.get("has_contact"):
            ats_flaws.append("clickable email")
        if not ats.get("has_links"):
            ats_flaws.append("GitHub/LinkedIn URLs")
        if ats_flaws:
            feedback["ats"] = f"Fix ATS gaps: add {', '.join(ats_flaws)}."
        else:
            feedback["ats"] = f"Clean {ats['format']} layout ({ats['word_count']} words) with all core ATS sections detected."

        return feedback
