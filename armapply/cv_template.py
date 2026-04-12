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
UI systems, React Native, and delivering maintainable, scalable products.\
"""

SKILLS = {
    "Frontend": ["React", "React Native", "TypeScript", "Redux", "Zustand", "React Query", "Context API", "Expo", "Next.js", "Vite", "React Hook Form", "SCSS/SASS", "Tailwind CSS"],
    "Mobile": ["React Native + Expo", "iOS & Android Deployment", "BLE (Bluetooth Low Energy)", "HealthKit / Health Connect / Google Fit", "OTA Firmware Updates", "Push Notifications", "WebView", "Native Modules"],
    "Backend": ["Node.js", "Express.js", "REST APIs", "GraphQL"],
    "E-commerce": ["Shopify / Hydrogen", "Custom Checkout Flows", "Payment Gateways"],
    "Tools": ["Git", "GitHub", "Jira", "Figma", "Postman", "Sentry", "Docker (basic)", "CI/CD"],
    "Other": ["Responsive Design", "Performance Optimization", "GraphQL"],
}

EXPERIENCE = [
    {
        "company":  "ShellLogix",
        "url":      "https://shelllogix.com/",
        "role":     "Senior Frontend Engineer",
        "period":   "Oct 2021 – Present",
        "location": "Yerevan, Armenia",
        "bullets": [
            "Worked on various client projects as part of an outsource team, developing web and mobile applications using React, React Native, and TypeScript",
            "Collaborated with international clients and teams to deliver high-quality software solutions",
            "Built responsive web applications and cross-platform mobile apps with modern state management and secure authentication",
            "Contributed to multiple projects including e-commerce platforms, business management tools, and financial applications",
        ],
    },
    {
        "company":  "Opta Sports (by Stats Perform)",
        "url":      "https://www.statsperform.com/",
        "role":     "Data Researcher / Editor",
        "period":   "Nov 2016 – Mar 2025",
        "location": "Yerevan / Remote",
        "bullets": [
            "Managed, validated, and edited large-scale sports datasets across multiple leagues with 100% accuracy and strict deadlines",
            "Coordinated with global data teams to deliver real-time statistics to broadcasters, media partners, and betting companies",
            "Strong domain knowledge in sports data helped significantly in building accurate filtering and statistics-heavy UIs",
        ],
    },
]

PROJECTS = [
    ("Service Admin Panel",
     "React 19 · TypeScript · Vite · Ant Design · React Query · Zustand",
     "Enterprise admin dashboard for user segmentation and marketing campaigns. Features real-time analytics, advanced filtering, and integrations with HubSpot and Communication Service."),
    ("Mobile E-commerce Solution",
     "React Native · React · TypeScript · Node.js · Express · Shopify API",
     "Ready-to-use mobile shopping applications for 100+ brands from Russia, UK, and US. Solutions for various industries from pharmacy to automobile."),
    ("Smart Device & Health Mobile App",
     "React Native · TypeScript · Expo · BLE · HealthKit · Google Fit · OTA Firmware",
     "Cross-platform mobile app for connected smart devices and health data. BLE device pairing, OTA firmware updates, and sync with major health platforms."),
    ("AI Social Media Post Generator",
     "React · TypeScript · AI Integration · Video Processing",
     "Business tool for generating social media posts using multiple AI models. Generates videos by parts and seamlessly connects them together. Built for US client."),
    ("RV Rental Platform",
     "Shopify · React · TypeScript · Custom Checkout · Booking System",
     "US-based company project for RV rental services. Custom Shopify solution allowing clients to rent RV vehicles and book campgrounds."),
    ("Cryptocurrency Admin Panel",
     "React · TypeScript · Redux · WebSocket · Chart.js",
     "Admin panel for cryptocurrency exchange and statistics. Built for a Ukrainian company. Features real-time crypto exchange rates and trading statistics."),
    ("Git Identity Management CLI",
     "TypeScript · Node.js · Commander.js",
     "npm package for managing multiple Git identities. Easily switch between work, personal, and organization accounts with cross-platform support. Published on npmjs."),
    ("Mobile WebView Application",
     "React Native · TypeScript · WebView · Per-App VPN",
     "React Native application with WebView integration for displaying company website. Main feature is per-app VPN functionality that users can enable directly in the app."),
    ("Accounting Company Website",
     "Next.js · React · Redux · Canvas API",
     "Multi-language web application for accounting services in Armenia. Features canvas forms and form-to-email functionality."),
]

EDUCATION = [
    {
        "institution": "Armenian National Agrarian University",
        "degree":      "Bachelor's Degree in Agriculture Marketing & Business",
        "period":      "Sep 2018 – Jun 2022",
        "note":        "Relevant coursework: Economics, Statistics, Project Management",
    }
]

LANGUAGES = [
    ("Armenian", "Native / First Language"),
    ("Russian",  "C1 – Advanced"),
    ("English",  "B2 – Upper-Intermediate (professional working proficiency)"),
]

ADDITIONAL = [
    "Available for full-time remote or relocation (EU, USA, Canada preferred)",
    "Passionate about clean code, performance, and beautiful UI/UX",
    "Active open-source contributor on GitHub",
    "Fast learner – successfully transitioned from sports data industry to full-time software engineering",
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
