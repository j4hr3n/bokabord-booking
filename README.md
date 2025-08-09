## Bokabord Punk Royale Availability Checker

Checks Punk Royale availability via Bokabord widget API and notifies an `ntfy` topic when matches are found.

- **Bokabord endpoint**: `https://app.bokabord.se/booking-widget/api/getTimes`
- **ntfy publishing docs**: see [ntfy: Publishing](https://docs.ntfy.sh/publish/)

### Quick start

1. Python 3.10+
2. Install deps:
   ```bash
   pip install -r requirements.txt
   ```
3. Configure `config.yaml` as needed (dates, party size, filters, ntfy topic).
4. Run locally:
   ```bash
   python bokabord_checker/main.py --debug --dry-run
   ```

### Configuration (`config.yaml`)

Key fields:

- **endpoint_url**: Bokabord `getTimes` API
- **payload_template**: Base payload; the script injects `date`, `amount`, and `mealid`
- **date_selection**: Default to all Fridays in November (current year). Use `specific_dates` to pin exact dates.
- **party_size**: Number in the party
- **time_filters**: Use `earliest`/`latest` or `allowlist` of explicit `HH:MM` times
- **ntfy**: `server`, `topic`, optional `priority`, and `title`

The payload contains opaque widget keys (e.g., `TkI3MHBxR1JGRjUrNVE9PQ`) and a `hash`. These were captured from a known-good request; keep them as-is unless you know they need to change.

### CLI overrides

- `--party 7`
- `--dates 2025-11-07,2025-11-14`
- `--month 11 --year 2025 --dow Friday`
- `--time-window 17:00-22:30`
- `--allowlist 18:30,19:00`
- `--ntfy-topic j4hr3n`
- `--dry-run` and `--debug`

Environment equivalents: `CONFIG`, `PARTY_SIZE`, `DATES`, `MONTH`, `YEAR`, `DOW`, `TIME_WINDOW`, `ALLOWLIST`, `NTFY_TOPIC`.

### GitHub Actions

Workflow scheduled daily; also supports manual `workflow_dispatch` with inputs. It installs dependencies and runs the checker with defaults from `config.yaml`.

### Notes

- If the Bokabord API changes its required fields, update `config.yaml` accordingly.
- The checker attempts to extract times from multiple response shapes and is resilient to minor changes.
- ntfy usage is based on the official docs. See [ntfy: Publishing](https://docs.ntfy.sh/publish/).
