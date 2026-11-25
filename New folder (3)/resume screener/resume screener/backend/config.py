# Job Roles, Skill Aliases, and Implied Competency Configuration

SKILL_ALIASES = {
    "HTML": ["HTML5", "JSX", "Semantic HTML"],
    "CSS": ["CSS3", "SCSS", "SASS", "Tailwind", "Tailwind CSS", "Bootstrap", "Styled Components"],
    "JavaScript": ["JS", "ES6", "ES6+", "ECMAScript"],
    "TypeScript": ["TS"],
    "React": ["React.js", "ReactJS", "React Native"],
    "Next.js": ["NextJS", "Next"],
    "Angular": ["AngularJS"],
    "Vue.js": ["Vue", "VueJS"],
    "Node.js": ["NodeJS", "Node"],
    "Express.js": ["ExpressJS", "Express"],
    "REST APIs": ["REST", "REST API", "RESTful API", "RESTful APIs", "RESTful Web Services"],
    "RESTful Web Services": ["REST", "REST API", "REST APIs", "RESTful APIs"],
    "SQL": ["MySQL", "PostgreSQL", "Postgres", "SQLite", "SQL Server", "T-SQL", "PL/SQL"],
    "MongoDB": ["Mongoose", "NoSQL"],
    "Git": ["Git CLI", "Version Control"],
    "GitHub": ["GitLab", "Bitbucket"],
    "Authentication": ["Auth", "JWT", "OAuth", "OAuth2", "SSO", "Passport.js", "NextAuth"],
    "Authorization": ["RBAC", "Access Control", "IAM", "JWT", "OAuth2"],
    "Deployment": ["CI/CD", "Docker", "AWS", "Vercel", "Netlify", "Render", "Heroku", "Cloud Deployment"],
    "AWS": ["Amazon Web Services", "EC2", "S3", "Lambda", "ECS", "EKS"],
    "Docker": ["Containerization", "Dockerfile", "Docker Compose"],
    "Kubernetes": ["K8s", "Helm"],
    "CI/CD": ["GitHub Actions", "Jenkins", "GitLab CI", "CircleCI"],
    "Terraform": ["IaC", "Infrastructure as Code"],
    "Linux": ["Ubuntu", "Bash", "Shell Scripting"],
    "Python": ["Python3"],
    "Machine Learning": ["ML", "Scikit-learn", "sklearn", "XGBoost", "LightGBM"],
    "Deep Learning": ["DL", "CNN", "RNN", "LSTM", "Transformers"],
    "Natural Language Processing": ["NLP", "LLMs", "LangChain", "Hugging Face", "RAG"],
    "NLP": ["Natural Language Processing", "LLMs", "LangChain", "RAG"],
    "Computer Vision": ["OpenCV", "YOLO", "MediaPipe", "Image Processing"],
    "Data Visualization": ["Matplotlib", "Seaborn", "Plotly", "Power BI", "Tableau"],
    "Core Java": ["Java", "Java 8", "Java 11", "Java 17", "J2EE"],
    "Object Oriented Programming": ["OOP", "OOPs"],
    "OOP": ["Object Oriented Programming", "OOPs"],
    "Data Structures": ["DSA", "Data Structures and Algorithms"],
    "Algorithms": ["DSA", "Data Structures and Algorithms"],
    "Unit Testing": ["JUnit", "PyTest", "Jest", "Vitest", "Mocha", "Mockito"],
    "JUnit": ["Unit Testing", "Mockito"],
    "Automated Testing": ["Selenium", "Playwright", "Cypress", "Appium"],
    "Penetration Testing": ["Pentesting", "Metasploit", "Burp Suite", "Nmap", "OWASP"],
    "Intrusion Detection Systems": ["IDS", "IPS", "Snort", "Suricata"],
    "SIEM Tools": ["SIEM", "Splunk", "QRadar", "ELK", "Wazuh"],
    "Power BI": ["Tableau", "Looker", "Data Visualization"],
    "Excel": ["Microsoft Excel", "Spreadsheets", "Pivot Tables"],
    "Flutter": ["Dart"],
    "React Native": ["Expo", "Mobile Development"],
    "Android": ["Kotlin", "Java Android", "Jetpack Compose"],
    "iOS": ["Swift", "SwiftUI"],
}

# If a candidate demonstrates a higher-level framework/tool, these foundational skills are credibly implied
IMPLIED_SKILLS = {
    "React": ["HTML", "CSS", "JavaScript"],
    "Next.js": ["React", "HTML", "CSS", "JavaScript"],
    "Angular": ["HTML", "CSS", "TypeScript", "JavaScript"],
    "Vue.js": ["HTML", "CSS", "JavaScript"],
    "Express.js": ["Node.js", "JavaScript", "REST APIs"],
    "Node.js": ["JavaScript"],
    "Spring Boot": ["Core Java", "Spring", "Object Oriented Programming", "RESTful Web Services"],
    "Spring": ["Core Java", "Object Oriented Programming"],
    "Hibernate": ["Core Java", "SQL", "JDBC"],
    "PyTorch": ["Python", "Machine Learning", "Deep Learning", "Neural Networks"],
    "TensorFlow": ["Python", "Machine Learning", "Deep Learning", "Neural Networks"],
    "Pandas": ["Python", "Data Cleaning"],
    "NumPy": ["Python"],
    "Vercel": ["Deployment"],
    "Netlify": ["Deployment"],
    "AWS": ["Deployment"],
    "Docker": ["Deployment", "Linux"],
    "Kubernetes": ["Docker", "Deployment"],
    "GitHub": ["Git"],
    "Authentication": ["Authorization"],
    "Authorization": ["Authentication"],
    "Penetration Testing": ["Vulnerability Assessment", "Network Security"],
    "React Native": ["React", "JavaScript"],
    "Flutter": ["Mobile Architecture", "REST APIs"],
    "Playwright": ["Automated Testing", "JavaScript"],
    "Cypress": ["Automated Testing", "JavaScript"],
    "Selenium": ["Automated Testing"],
}

JOB_ROLES = {
    "FullStackDeveloper": {
        "display_name": "Full Stack Developer",
        "domain": "Web",
        "min_experience": 2,
        "skills_required": [
            "HTML", "CSS", "JavaScript", "React", "Node.js", "Express.js",
            "MongoDB", "SQL", "REST APIs", "Git", "GitHub",
            "Authentication", "Authorization", "Deployment", "AWS", "Docker"
        ]
    },
    "FrontendDeveloper": {
        "display_name": "Frontend Developer",
        "domain": "Web",
        "min_experience": 1,
        "skills_required": [
            "HTML", "CSS", "JavaScript", "TypeScript", "React", "Next.js",
            "State Management", "REST APIs", "Responsive Design",
            "Web Accessibility", "Unit Testing", "Git", "Performance Optimization"
        ]
    },
    "BackendDeveloper": {
        "display_name": "Backend Developer",
        "domain": "Web",
        "min_experience": 2,
        "skills_required": [
            "Python", "Node.js", "SQL", "MongoDB", "REST APIs", "Authentication",
            "Authorization", "Caching", "Redis", "Docker", "Microservices",
            "Unit Testing", "Git", "System Design", "AWS"
        ]
    },
    "JavaFullStackDeveloper": {
        "display_name": "Java Full Stack Developer",
        "domain": "Web",
        "min_experience": 2,
        "skills_required": [
            "Core Java", "Object Oriented Programming", "Data Structures",
            "Algorithms", "Spring", "Spring Boot", "Hibernate", "JDBC",
            "SQL", "RESTful Web Services", "Microservices", "React", "Git",
            "Unit Testing", "JUnit"
        ]
    },
    "AIMLEngineer": {
        "display_name": "AI / ML Engineer",
        "domain": "AI",
        "min_experience": 2,
        "skills_required": [
            "Python", "Machine Learning", "Deep Learning", "Neural Networks",
            "TensorFlow", "PyTorch", "Natural Language Processing", "NLP",
            "Computer Vision", "Data Preprocessing", "Model Deployment",
            "Mathematics", "Linear Algebra", "Optimization Techniques", "Docker"
        ]
    },
    "DataScienceEngineer": {
        "display_name": "Data Science Engineer",
        "domain": "AI",
        "min_experience": 2,
        "skills_required": [
            "Python", "Statistics", "Probability", "Pandas", "NumPy",
            "Data Visualization", "Machine Learning", "SQL", "Data Cleaning",
            "Feature Engineering", "Model Evaluation", "Jupyter Notebook"
        ]
    },
    "DataAnalyst": {
        "display_name": "Data Analyst",
        "domain": "Data",
        "min_experience": 1,
        "skills_required": [
            "SQL", "Python", "Excel", "Power BI", "Data Visualization",
            "Data Cleaning", "Statistics", "Pandas", "Reporting",
            "ETL", "A/B Testing", "Dashboarding"
        ]
    },
    "DevOpsCloudEngineer": {
        "display_name": "DevOps & Cloud Engineer",
        "domain": "Cloud",
        "min_experience": 2,
        "skills_required": [
            "Linux", "Docker", "Kubernetes", "AWS", "CI/CD", "Terraform",
            "Git", "Monitoring", "Networking", "Python", "Shell Scripting",
            "Security", "Load Balancing", "Deployment"
        ]
    },
    "Cybersecurity": {
        "display_name": "Cybersecurity Engineer",
        "domain": "Security",
        "min_experience": 2,
        "skills_required": [
            "Network Security", "Cryptography", "Ethical Hacking",
            "Penetration Testing", "Linux", "Firewalls",
            "Intrusion Detection Systems", "Vulnerability Assessment",
            "SIEM Tools", "Incident Response", "Risk Management",
            "Cyber Laws", "Compliance"
        ]
    },
    "MobileAppDeveloper": {
        "display_name": "Mobile App Developer",
        "domain": "Mobile",
        "min_experience": 1,
        "skills_required": [
            "React Native", "Flutter", "Android", "iOS", "JavaScript",
            "TypeScript", "REST APIs", "State Management", "Mobile Architecture",
            "SQLite", "Firebase", "Git", "App Store Deployment"
        ]
    },
    "QAEngineer": {
        "display_name": "QA & Automation Engineer",
        "domain": "Quality",
        "min_experience": 1,
        "skills_required": [
            "Automated Testing", "Unit Testing", "Selenium", "Playwright",
            "Cypress", "API Testing", "Postman", "SQL", "JavaScript",
            "Python", "CI/CD", "Bug Tracking", "Git"
        ]
    }
}
