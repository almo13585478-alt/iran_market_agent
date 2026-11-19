"""Fix line 784 indentation + insert min_bars if missing."""

PATH = "dashboard/main.py"

with open(PATH, "r", encoding="utf-8-sig") as f:
    content = f.read()

if content.startswith("\ufeff"):
    content = content[1:]

lines = content.split("\n")

# --- Diagnostic ---
print(f"Total lines: {len(lines)}")
print(f"btn_rank count: {content.count('btn_rank')}")
print(f"progress.empty count: {content.count('progress.empty()')}")

# --- Find and fix ---
found = False
for i, line in enumerate(lines):
    if 'if st.button("محاسبه رتبه‌بندی"' in line and "btn_rank" in line:
        found = True
        old_indent = len(line) - len(line.lstrip())

        # Fix indentation to 8
        if old_indent != 8:
            lines[i] = "        " + line.lstrip()
            print(f"\n✅ خط {i+1}: indent {old_indent} → 8")
        else:
            print(f"\n✓ خط {i+1}: indent از قبل 8 بود")

        # Check for min_bars in the 15 lines before
        before = "\n".join(lines[max(0, i - 15):i])
        if "min_bars = st.number_input" not in before:
            lines.insert(
                i,
                '        min_bars = st.number_input("حداقل رکورد", 30, 300, 60, 10, key="rk_min")',
            )
            print(f"✅ خط {i+1}: min_bars درج شد")
        else:
            print(f"✓ min_bars از قبل وجود داشت")

        break

if not found:
    print("\n❌ خط `if st.button(\"محاسبه رتبه‌بندی\"` پیدا نشد!")
    exit(1)

# --- Write ---
new_content = "\n".join(lines)
with open(PATH, "w", encoding="utf-8") as f:
    f.write(new_content)
print(f"\n✅ نوشته شد ({len(new_content)} کاراکتر، بدون BOM)")

# --- Verify ---
import ast
try:
    ast.parse(new_content)
    print("✅ ast.parse: main OK")
except SyntaxError as e:
    print(f"\n❌ هنوز خطا: خط {e.lineno}: {e.msg}")
    print(f"   متن: {repr(e.text)}")
    # Show context
    bad = e.lineno - 1
    for j in range(max(0, bad - 3), min(len(lines), bad + 4)):
        prefix = ">>>" if j == bad else "   "
        leading = len(lines[j]) - len(lines[j].lstrip())
        print(f"{prefix} {j+1:4d} [{leading:2d}] {lines[j][:100]}")