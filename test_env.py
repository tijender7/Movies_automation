from dotenv import load_dotenv
import os
from pathlib import Path

DOTENV_PATH = Path(__file__).parent / ".env"
print("Loading:", DOTENV_PATH)
# Print file existence and contents
print("EXISTS:", DOTENV_PATH.exists())
if DOTENV_PATH.exists():
    print("CONTENTS:")
    print(repr(DOTENV_PATH.read_text(encoding="utf-8")))
else:
    print("File does not exist!")

load_dotenv(dotenv_path=DOTENV_PATH)
print("TAVILY_API_KEY:", repr(os.getenv("TAVILY_API_KEY")))

# Print all envs starting with TAVILY
for k, v in os.environ.items():
    if k.startswith("TAVILY"):
        print(f"DEBUG ENV: {k} = {repr(v)}")
