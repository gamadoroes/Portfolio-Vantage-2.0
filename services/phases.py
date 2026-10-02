"""Phase 1-7 registry, ported from static/app.js's PHASE_DEFINITIONS (app.js:66-95).

This is the first time this registry exists on the backend — previously it
was defined only in browser JS. Kept byte-identical to the JS version so
reconstructed insights.json content matches exactly.
"""

PHASE_DEFINITIONS = {
    "1": {
        "title": "The Landscape",
        "description": (
            "Comprehensive market review using HEIMS and publicly available data — "
            "program types, top-ranked offerings, enrolment trends, provider landscape, "
            "and general sentiment analysis around program value. Focused on current "
            "data (last 2 years)."
        ),
    },
    "2": {
        "title": "The Student",
        "description": (
            "Target student demographics, motivations, pain points, decision drivers, "
            "career stage profiles, and enrolment pathway analysis across competitor "
            "programs."
        ),
    },
    "3": {
        "title": "Review of Marketing",
        "description": (
            "Competitor scoping, website UX/messaging review, sentiment analysis via "
            "social listening, paid and organic channel strategy, and brand "
            "positioning analysis."
        ),
    },
    "4": {
        "title": "Product Features",
        "description": (
            "Granular course-level scraping — delivery modes, unit structures, "
            "specialisations, pricing, duration, flexibility options, technology "
            "platforms, and student experience features."
        ),
    },
    "5": {
        "title": "Academic Content",
        "description": (
            "Deep curriculum analysis for priority competitors — learning outcomes, "
            "assessment design, accreditation, faculty profiles, academic "
            "partnerships, and pedagogical approach."
        ),
    },
    "6": {
        "title": "Industry Engagement",
        "description": (
            "Industry partnerships, employer connections, placement programs, "
            "advisory boards, professional body affiliations, and work-integrated "
            "learning arrangements."
        ),
    },
    "7": {
        "title": "Options for OES",
        "description": (
            "White space analysis and strategic options using the SO WHAT / NOW WHAT "
            "framework — interrogating each key finding across all phases to identify "
            "actionable opportunities for OES."
        ),
    },
}

PHASE_KEYS = list(PHASE_DEFINITIONS.keys())
