"""One-shot fix for main.py indentation issues."""

PATH = "dashboard/main.py"

with open(PATH, "r", encoding="utf-8-sig") as f:
    content = f.read()

# Strip BOM if any
if content.startswith("\ufeff"):
    content = content[1:]

lines = content.split("\n")
fixed = 0

for i, line in enumerate(lines):
    stripped = line.strip()

    # Fix progress.empty() — should be 20 spaces (inside else: block)
    if stripped == "progress.empty()":
        current = len(line) - len(line.lstrip())
        if current != 20:
            lines[i] = " " * 20 + "progress.empty()"
            print(f"Line {i+1}: progress.empty() {current} -> 20")
            fixed += 1

with open(PATH, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"\n✅ Fixed {fixed} line(s). No BOM written.")

# Verify
import ast
try:
    ast.parse(open(PATH, encoding="utf-8").read())
    print("✅ ast.parse: main OK")
except SyntaxError as e:
    print(f"❌ Still has error: line {e.lineno}: {e.msg}")
    print(f"   Text: {e.text}")