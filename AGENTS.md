# DodoStack

- Run the workspace and its services with `.venv/bin/python server.py` from the repository root. Install dependencies with `python -m pip install -r requirements.txt`.
- Use the shared DodoStack identity in `dodo/auth.py`. Require sign-in on service routes, check ownership with the shared account ID, and validate CSRF tokens on state-changing browser requests.
- Keep each service in `subsites/`. Register it in `SERVICE_MODULES` and `BRAND_FILES` in `server.py` and add its name and description in `dodo/pages.py`. Link workspace cards directly to their services.
- Keep local data and signing keys out of Git. Services initialize their databases from the current schema. Run destructive data operations only when explicitly requested, and use isolated data for verification.
- DodoKey encrypts and decrypts vault entries in the browser. Keep vault passwords, derived keys, and unencrypted entries out of requests, logs, and persistent browser storage. Keep scripts and styles local to DodoKey.
- Verify changes through the normal workspace and service routes. Check signed-out access and account isolation when editing authentication or personal data.
- Use .venv as python environment.
- Always when you learn something new about this project that would help in developing it later write it into this file.
- Use a referrer policy such as `strict-origin` for native POST forms. With `no-referrer`, browsers send `Origin: null` and the shared origin check rejects the form.
- Dodo Calender custom-day forms use day.month or day.month.year. The database stores month-day or ISO dates for calendar matching and sorting.
