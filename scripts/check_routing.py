"""
Diagnostic: verify that main.py has all pages wired correctly.
Run: python -m scripts.check_routing
"""

import ast
import sys
from pathlib import Path


def main():
    main_py = Path("dashboard/main.py")
    if not main_py.exists():
        print(f"❌ {main_py} پیدا نشد")
        sys.exit(1)

    src = main_py.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Extract the sidebar.radio page list (first positional list after "صفحه اصلی")
    page_list = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if (hasattr(node.func, "attr") and node.func.attr == "radio"):
                for arg in node.args:
                    if isinstance(arg, ast.List):
                        page_list = [
                            elt.value for elt in arg.elts
                            if isinstance(elt, ast.Constant)
                        ]
                        break

    # Extract all elif branches: `elif page == "..."` patterns
    branches = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
            comp = node.test
            if (isinstance(comp.left, ast.Name)
                    and comp.left.id == "page"
                    and comp.ops
                    and isinstance(comp.ops[0], ast.Eq)
                    and comp.comparators
                    and isinstance(comp.comparators[0], ast.Constant)):
                branches.append(comp.comparators[0].value)

    print("=" * 60)
    print(f"Pages in sidebar radio: {len(page_list or [])}")
    print("=" * 60)
    for p in (page_list or []):
        mark = "✓" if p in branches else "✗ NO ROUTE"
        print(f"  {mark}  {p}")

    print()
    print("=" * 60)
    print(f"Branches in code: {len(branches)}")
    print("=" * 60)
    for b in branches:
        mark = "✓" if page_list and b in page_list else "✗ NOT IN SIDEBAR"
        print(f"  {mark}  {b}")

    print()
    missing = [p for p in (page_list or []) if p not in branches]
    if missing:
        print(f"❌ صفحات بدون branch: {missing}")
        print("   → این صفحات در sidebar دیده می‌شوند ولی کلیک = صفحه سفید")
    else:
        print("✅ همه صفحات branch دارند")

    # Special check for assistant
    print()
    if "🤖 دستیار" in (page_list or []):
        if "🤖 دستیار" in branches:
            print("✅ «🤖 دستیار» هم در sidebar و هم branch هست")
            # Check if assistant.py exists
            ap = Path("dashboard/pages/assistant.py")
            if ap.exists():
                print(f"✅ {ap} موجود ({ap.stat().st_size} بایت)")
            else:
                print(f"❌ {ap} نیست")
        else:
            print("❌ «🤖 دستیار» در sidebar هست ولی branch ندارد")
    else:
        print("❌ «🤖 دستیار» در sidebar نیست")


if __name__ == "__main__":
    main()