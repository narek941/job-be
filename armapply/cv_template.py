"""
cv_template.py  —  Narek Qolyan's canonical CV data + text/PDF generator.
The tailor step uses this as the base and only modifies the skills/summary
sections to match a specific job.
"""
from __future__ import annotations

# ── Canonical CV data (from narek-qolyan-cv / cv.const.ts) ─────────────────

NAME               = "Narek Kolyan"
TITLE              = "Senior Frontend Engineer"
EMAIL              = "nqolyan@gmail.com"
PHONE              = "+374 55 818 286"
LOCATION           = "Yerevan, Armenia"
GITHUB             = "https://github.com/narek941"
LINKEDIN           = "https://linkedin.com/in/narek-qolyan-4a92b611b"

SUMMARY = """\
Senior Frontend Engineer with 6+ years of experience building scalable web \
and mobile applications using React, TypeScript, and modern frontend \
architectures. Specialized in headless e-commerce, GraphQL data layers, and \
performance optimization for high-traffic applications. Experienced in modular \
UI systems, React Native, and delivering maintainable, scalable products. \
Open to remote opportunities.\
"""

SKILLS = {
    "Frontend":   ["React", "React Native", "TypeScript", "Next.js", "Redux",
                   "Zustand", "React Query", "Context API", "Expo", "Vite",
                   "React Hook Form", "SCSS/SASS", "Tailwind CSS"],
    "Mobile":     ["React Native + Expo", "iOS & Android Deployment",
                   "BLE (Bluetooth Low Energy)", "HealthKit / Health Connect",
                   "OTA Firmware Updates", "Push Notifications", "WebView",
                   "Native Modules"],
    "Backend":    ["Node.js", "Express.js", "REST APIs", "GraphQL"],
    "E-commerce": ["Shopify / Hydrogen", "Custom Checkout Flows",
                   "Payment Gateways"],
    "Tools":      ["Git", "GitHub", "Jira", "Figma", "Postman", "Sentry",
                   "Docker (basic)", "CI/CD"],
}

EXPERIENCE = [
    {
        "company":  "ShellLogix",
        "url":      "https://shelllogix.com/",
        "role":     "Senior Frontend Engineer",
        "period":   "Oct 2021 – Present",
        "location": "Yerevan, Armenia",
        "bullets": [
            "Led frontend development of 6+ client projects across web and mobile (React, React Native, TypeScript)",
            "Built headless e-commerce solutions (Shopify/Hydrogen) with custom checkout for US & UK brands",
            "Developed enterprise admin dashboards (React 19, Vite, Ant Design, Zustand, React Query) for 50-person company",
            "Delivered Smart Device & Health mobile app with BLE pairing, OTA firmware updates, HealthKit/Google Fit sync",
            "Built AI Social Media Post Generator integrating multiple AI models with automated video stitching",
            "Mentored junior developers; promoted clean architecture and code-review culture",
        ],
    },
    {
        "company":  "Opta Sports (by Stats Perform)",
        "url":      "https://www.statsperform.com/",
        "role":     "Data Researcher / Editor",
        "period":   "Nov 2016 – Mar 2025",
        "location": "Yerevan / Remote",
        "bullets": [
            "Managed and validated large-scale sports datasets for global broadcasters, media and betting companies",
            "Maintained 100% accuracy under strict real-time deadlines across multiple international leagues",
            "Domain expertise in data-heavy UIs informed frontend filtering and statistics components",
        ],
    },
]

PROJECTS = [
    ("Service Admin Panel",
     "React 19 · TypeScript · Vite · Ant Design · React Query · Zustand",
     "Real-time dashboard with user segmentation, advanced filtering, HubSpot & Communication Service integration for 50-person company."),
    ("Mobile E-commerce Solution",
     "React Native · TypeScript · Node.js · Shopify API · Push Notifications",
     "Ready-to-use mobile shopping apps for 100+ brands (pharmacy → automobile), 5+ analytics variants, custom backend."),
    ("Smart Device & Health App",
     "React Native · Expo · BLE · HealthKit · Health Connect · OTA Firmware",
     "Cross-platform app for smart wearables — BLE pairing, OTA updates, health-platform sync."),
    ("Git Identity Management CLI",
     "TypeScript · Node.js · Commander.js · Jest",
     "npm package for multi-identity Git management with SSH switching. 50+ unit tests. Published on npmjs."),
    ("AI Social Media Generator",
     "React · TypeScript · Multi-AI APIs · Video Processing",
     "Automated social-media content pipeline generating and stitching AI videos for US client."),
    ("Cryptocurrency Admin Panel",
     "React · TypeScript · Redux · WebSocket · Chart.js",
     "Real-time crypto exchange dashboard with live charts and admin tooling."),
]

EDUCATION = [
    {
        "institution": "Armenian National Agrarian University",
        "degree":      "Bachelor's Degree — Agriculture Marketing & Business",
        "period":      "Sep 2018 – Jun 2022",
        "note":        "Coursework: Economics, Statistics, Project Management",
    }
]

LANGUAGES = [
    ("Armenian", "Native"),
    ("Russian",  "C1 — Advanced"),
    ("English",  "B2 — Upper-Intermediate (professional working proficiency)"),
]

ADDITIONAL = [
    "Open to full-time remote or relocation (EU, USA, Canada preferred)",
    "Passionate about clean code, performance, and exceptional UI/UX",
    "Published npm package: use-multiple-gits (open-source)",
    "Fast self-learner — transitioned from sports data industry to senior engineering",
]


# ── Plain-text CV renderer (matches the layout of the Next.js CV page) ─────

def render_cv_text(
    job_title: str = "",
    extra_skills: list[str] | None = None,
    tailored_summary: str | None = None,
) -> str:
    """
    Render the CV as plain text.
    If tailored_summary is provided, it replaces the default summary.
    Extra skills (from job requirements) are highlighted at the top of the Skills section.
    """
    lines: list[str] = []

    def hr(char="─", width=70):
        lines.append(char * width)

    def section(title: str):
        lines.append("")
        lines.append(title.upper())
        hr()

    # ── Header ──────────────────────────────────────────────────────────────
    lines.append(NAME)
    lines.append(TITLE)
    lines.append(f"{EMAIL}  │  {PHONE}  │  {LOCATION}")
    lines.append(f"GitHub: {GITHUB}")
    lines.append(f"LinkedIn: {LINKEDIN}")

    # ── Summary ─────────────────────────────────────────────────────────────
    section("Professional Summary")
    lines.append(tailored_summary or SUMMARY)

    # ── Skills ──────────────────────────────────────────────────────────────
    section("Technical Skills")
    if extra_skills:
        lines.append(f"Key skills for this role: {', '.join(extra_skills)}")
        lines.append("")
    for category, items in SKILLS.items():
        lines.append(f"  {category}:")
        lines.append(f"    {', '.join(items)}")

    # ── Experience ──────────────────────────────────────────────────────────
    section("Professional Experience")
    for exp in EXPERIENCE:
        lines.append(f"{exp['role']}  —  {exp['company']}")
        lines.append(f"{exp['period']}  │  {exp['location']}")
        for b in exp["bullets"]:
            lines.append(f"  • {b}")
        lines.append("")

    # ── Projects ────────────────────────────────────────────────────────────
    section("Selected Projects")
    for name, tech, desc in PROJECTS:
        lines.append(f"▸ {name}")
        lines.append(f"  Tech: {tech}")
        lines.append(f"  {desc}")
        lines.append("")

    # ── Education ───────────────────────────────────────────────────────────
    section("Education")
    for ed in EDUCATION:
        lines.append(ed["institution"])
        lines.append(ed["degree"])
        lines.append(f"{ed['period']}  │  {ed['note']}")

    # ── Languages ───────────────────────────────────────────────────────────
    section("Languages")
    for lang, level in LANGUAGES:
        lines.append(f"  {lang}: {level}")

    # ── Additional ──────────────────────────────────────────────────────────
    section("Additional")
    for item in ADDITIONAL:
        lines.append(f"  • {item}")

    return "\n".join(lines)
