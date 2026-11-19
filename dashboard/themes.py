"""
Dashboard themes: light (default) and dark.
All colors use CSS variables so both themes work seamlessly.
"""

_LIGHT_VARS = """
:root {
    --bg-main: #f5f7fa;
    --bg-sidebar: #ffffff;
    --bg-card: #ffffff;
    --bg-card-alt: #f8fafc;
    --bg-card-hover: #f0f4f8;
    --text-primary: #1a1f29;
    --text-secondary: #4a5461;
    --text-muted: #8a95a5;
    --border: #e1e5ea;
    --border-strong: #d1d5db;
    --primary: #008e73;
    --primary-light: #00d4aa;
    --primary-dark: #006655;
    --danger: #dc2626;
    --warning: #d97706;
    --info: #0284c7;
    --success: #059669;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.04);
    --shadow-md: 0 4px 12px rgba(0,0,0,0.08);
}
"""

_DARK_VARS = """
:root {
    --bg-main: #0a0e14;
    --bg-sidebar: #0d1117;
    --bg-card: rgba(255,255,255,0.04);
    --bg-card-alt: rgba(255,255,255,0.02);
    --bg-card-hover: rgba(255,255,255,0.06);
    --text-primary: #e8ecf1;
    --text-secondary: #a0a8b0;
    --text-muted: #666;
    --border: rgba(255,255,255,0.08);
    --border-strong: rgba(255,255,255,0.15);
    --primary: #00d4aa;
    --primary-light: #00d4aa;
    --primary-dark: #008e73;
    --danger: #ff5252;
    --warning: #ffab40;
    --info: #40c4ff;
    --success: #26a69a;
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.3);
    --shadow-md: 0 4px 12px rgba(0,0,0,0.4);
}
"""

_COMMON_CSS = """
html, body, [class*="css"] {
    direction: rtl;
    text-align: right;
    font-family: 'Segoe UI', 'Tahoma', sans-serif;
    color: var(--text-primary);
}
.stApp { background: var(--bg-main); }
.stMetricValue {
    direction: ltr;
    font-family: 'Segoe UI', 'Tahoma', sans-serif;
    font-weight: 700 !important;
    color: var(--text-primary) !important;
}
div[data-testid="stSidebar"] {
    direction: rtl;
    background: var(--bg-sidebar);
    border-left: 1px solid var(--border);
}
h1 { color: var(--text-primary) !important; font-size: 32px !important; margin-bottom: 12px !important; }
h2 {
    color: var(--text-primary) !important;
    font-size: 22px !important;
    border-bottom: 2px solid var(--primary);
    padding-bottom: 6px;
}
h3 { color: var(--text-primary) !important; font-size: 18px !important; }
label { color: var(--text-secondary) !important; }
p, span, div { color: inherit; }
caption { color: var(--text-muted) !important; }
table { direction: rtl; }
thead { background: var(--bg-card-alt) !important; }
tbody tr:nth-child(even) { background: var(--bg-card-alt) !important; }
tbody tr:hover { background: var(--bg-card-hover) !important; }

.stButton > button {
    border-radius: 8px;
    transition: all 0.15s ease;
    font-weight: 600;
    background: var(--bg-card);
    color: var(--text-primary);
    border: 1px solid var(--border-strong);
}
.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: var(--shadow-md);
    border-color: var(--primary);
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, var(--primary-light) 0%, var(--primary-dark) 100%);
    color: #001a14;
    border: none;
}

div[data-testid="stMetric"] {
    background: var(--bg-card);
    border-radius: 10px;
    padding: 12px 14px;
    border: 1px solid var(--border);
    box-shadow: var(--shadow-sm);
}
div[data-testid="stMetric"]:hover { border-color: var(--primary); }

div[data-testid="stExpander"] {
    border: 1px solid var(--border);
    border-radius: 10px;
    background: var(--bg-card);
}

.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background: transparent;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px;
    padding: 8px 16px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    color: var(--text-secondary);
}
.stTabs [aria-selected="true"] {
    background: linear-gradient(135deg, var(--primary-dark) 0%, var(--primary) 100%) !important;
    color: #ffffff !important;
    font-weight: 700;
    border-color: transparent;
}

.stAlert { border-radius: 10px; }
.stTextInput input, .stNumberInput input, .stSelectbox select, .stTextArea textarea {
    background: var(--bg-card) !important;
    color: var(--text-primary) !important;
    border: 1px solid var(--border-strong) !important;
    border-radius: 6px !important;
}
.stChatMessage { border-radius: 10px; }

::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--bg-main); }
::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--primary); }
"""


def get_css(theme: str = "light") -> str:
    """Return full CSS for the requested theme."""
    vars_css = _DARK_VARS if theme == "dark" else _LIGHT_VARS
    return f"<style>{vars_css}{_COMMON_CSS}</style>"