import json, os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "skills", "software-factory", "scripts", "ledger.py")


def write_lines(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        for row in rows:
            handle.write((row if isinstance(row, str) else json.dumps(row)) + "\n")


def transcript(path, model, start, end):
    def turn(stamp, mid):
        return {"type": "assistant", "timestamp": stamp, "message": {
            "id": mid, "model": model, "content": [{"type": "text", "text": "x"}],
            "usage": {"input_tokens": 1000, "output_tokens": 500}}}
    write_lines(path, [turn(start, "m1"), turn(end, "m2")])


CODEX_LINES = [
    {"start": "2026-11-12T10:00:00Z", "end": "2026-11-12T10:05:00Z", "provider": "codex", "model": "gpt-6.1-sol",
     "label": "spec review 1", "phase": "Review", "exit": 0, "status": "ok",
     "input_tokens": 1000000, "cached_input_tokens": 200000, "output_tokens": 100000},
    {"start": "2026-11-12T10:06:00Z", "end": "2026-11-12T10:07:00Z", "provider": "codex", "model": "gpt-6-luna",
     "label": "final review 1", "phase": "Review", "exit": 9, "status": "wrong-target",
     "input_tokens": 2000000, "cached_input_tokens": 1000000, "output_tokens": 200000},
]


def codex_labels(data):
    return [a["label"] for a in data["agents"] if a.get("provider") == "codex"]


class LedgerTest(unittest.TestCase):
    """Codex rows in the ledger. The Claude session is found through run.json `sessions`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.home = os.path.join(t, "home")
        self.elsewhere = os.path.join(t, "elsewhere")
        for d in (self.home, self.elsewhere):
            os.makedirs(d)

    def run_ledger(self, factory, extra=()):
        env = dict(os.environ, HOME=self.home, CLAUDE_CONFIG_DIR=self.home)
        out = os.path.join(factory, "out.html")
        js = os.path.join(factory, "out.json")
        args = [sys.executable, LEDGER, "--factory", factory, "--out", out, "--json", js, *extra]
        done = subprocess.run(args, cwd=self.elsewhere, env=env, capture_output=True, text=True)
        data = None
        if os.path.exists(js):
            with open(js) as handle:
                data = json.load(handle)
        return done, data

    def make_session(self, name, ids):
        """A session folder with one workflow whose agents ran at 09:0x on the same day as CODEX_LINES."""
        session = os.path.join(self.home, "projects", "-fake", name)
        wf = os.path.join(session, "subagents", "workflows", "wf_1")
        write_lines(os.path.join(wf, "journal.jsonl"), [
            {"type": "started", "agentId": i, "label": f"{name} {i}", "phase": "Build"} for i in ids
        ] + ["not json"])
        for n, i in enumerate(ids):
            transcript(os.path.join(wf, f"agent-{i}.jsonl"), "claude-sonnet-5-5",
                       f"2026-11-12T09:0{n}:00Z", f"2026-11-12T09:0{n}:30Z")
        return name

    def make_factory(self, name, codex=None, started=None, review=None, ids=("a1",)):
        factory = os.path.join(self.tmp.name, name)
        os.makedirs(factory)
        run = {"base": "origin/main", "sessions": [self.make_session(name + "-lead", list(ids))]}
        if started:
            run["started"] = started
        if review or (review is None and codex is not None):
            run["review"] = review or "cross"
        with open(os.path.join(factory, "run.json"), "w") as handle:
            json.dump(run, handle)
        if codex is not None:
            write_lines(os.path.join(factory, "codex-runs.jsonl"), codex)
        return factory

    def codex_fixture(self, codex=CODEX_LINES, started=None):
        rows = None if codex is None else list(codex) + ["bad line"]
        return self.make_factory("factory", codex=rows, started=started, ids=("a1", "a2"))

    def page(self, factory):
        with open(os.path.join(factory, "out.html")) as handle:
            return handle.read()

    def test_codex_rows_priced(self):
        factory = self.codex_fixture()
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        codex = {a["label"]: a for a in data["agents"] if a["provider"] == "codex"}
        self.assertAlmostEqual(codex["spec review 1"]["cost"], 3.0, places=6)
        self.assertAlmostEqual(codex["final review 1"]["cost"], 0.21, places=6)
        self.assertEqual(sorted(a["provider"] for a in data["agents"]), ["claude", "claude", "codex", "codex"])
        starts = [a["start"] for a in data["agents"]]
        self.assertEqual(starts, sorted(starts))

    def test_provider_column_and_wrong_target(self):
        factory = self.codex_fixture()
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        page = self.page(factory)
        self.assertIn("<th>Provider</th>", page)
        self.assertIn("Codex runs", page)
        self.assertIn("wrong-target", page)
        self.assertIn("--codex", page)
        self.assertIn(".m-codex", page)
        self.assertIn("Cached input uses the cached price", page)

    def test_default_ledger_has_no_provider_column(self):
        factory = self.codex_fixture(codex=None)
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        page = self.page(factory)
        for text in ("Provider", "Codex", "--codex", "m-codex", "p-codex"):
            self.assertNotIn(text, page)
        self.assertTrue(all("provider" not in a for a in data["agents"]))

    def test_single_mode_ignores_codex_log(self):
        factory = self.make_factory("single", codex=CODEX_LINES, review="single")
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(data["agents"]), 1)
        self.assertNotIn("provider", data["agents"][0])
        page = self.page(factory)
        for text in ("Provider", "Codex", "--codex", "m-codex", "p-codex", "price unverified"):
            self.assertNotIn(text, page)

    def test_missing_review_key_is_single_mode(self):
        factory = self.make_factory("nokey", codex=CODEX_LINES)
        with open(os.path.join(factory, "run.json")) as handle:
            run = json.load(handle)
        run.pop("review", None)
        with open(os.path.join(factory, "run.json"), "w") as handle:
            json.dump(run, handle)
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(data["agents"]), 1)
        self.assertNotIn("provider", data["agents"][0])

    def test_unverified_price_is_marked(self):
        astra = dict(CODEX_LINES[0], model="gpt-6-astra", label="astra review")
        factory = self.make_factory("astra", codex=[astra, CODEX_LINES[1]])
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        page = self.page(factory)
        self.assertEqual(page.count("estimate, price unverified"), 2)  # the roster row and the TL;DR line
        self.assertIn("price unverified for some Codex rows", page)  # the TL;DR line
        flags = {a["label"]: a["price_unverified"] for a in data["agents"] if a["provider"] == "codex"}
        self.assertEqual(flags, {"astra review": True, "final review 1": False})

    def test_verified_price_is_not_marked(self):
        factory = self.codex_fixture()
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("price unverified", self.page(factory))

    def test_unknown_codex_model_costs_na(self):
        factory = self.make_factory("odd", codex=[dict(CODEX_LINES[0], model="gpt-9-mystery")])
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        codex = [a for a in data["agents"] if a["provider"] == "codex"]
        self.assertIsNone(codex[0]["cost"])
        self.assertIn("n/a", self.page(factory))

    def test_stale_lock_row_is_not_a_run(self):
        note = dict(CODEX_LINES[0], status="stale-lock", exit=None, label="stale")
        factory = self.make_factory("stale", codex=[note, CODEX_LINES[0]])
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(codex_labels(data), ["spec review 1"])

    def test_attempt_row_is_not_a_run(self):
        attempt = dict(CODEX_LINES[0], status="attempt", exit=None, label="attempt")
        factory = self.make_factory("attempt", codex=[attempt, CODEX_LINES[0]])
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(codex_labels(data), ["spec review 1"])

    def test_fallback_row_is_not_a_run(self):
        fallback = dict(CODEX_LINES[0], status="fallback", exit=None, label="fallback")
        factory = self.make_factory("fallback", codex=[fallback, CODEX_LINES[0]])
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(codex_labels(data), ["spec review 1"])

    def test_cancelled_row_still_counts_as_a_run(self):
        attempt = dict(CODEX_LINES[0], status="attempt", exit=None, label="attempt")
        cancelled = dict(CODEX_LINES[0], status="cancelled", exit=None, label="cancelled run")
        factory = self.make_factory("cancelled", codex=[attempt, cancelled])
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(codex_labels(data), ["cancelled run"])

    def test_since_cutoff_applies_to_codex(self):
        done, data = self.run_ledger(self.codex_fixture(), ["--since", "2026-11-12T10:03:00Z"])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(codex_labels(data), ["final review 1"])

    def test_rows_before_run_start_never_show(self):
        old = dict(CODEX_LINES[0], start="2026-11-11T08:00:00Z", end="2026-11-11T08:05:00Z", label="old run")
        factory = self.codex_fixture(codex=[old, CODEX_LINES[1]], started="2026-11-12T08:00:00Z")
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(codex_labels(data), ["final review 1"])
        self.assertNotIn("old run", self.page(factory))

    def test_label_and_phase_fallbacks(self):
        rows = [
            {k: v for k, v in CODEX_LINES[0].items() if k not in ("label", "phase")} | {"task_id": "task-7"},
            {k: v for k, v in CODEX_LINES[1].items() if k not in ("label", "phase")},
        ]
        factory = self.make_factory("fallbacks", codex=rows)
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        got = [(a["label"], a["phase"]) for a in data["agents"] if a["provider"] == "codex"]
        self.assertEqual(got, [("task-7", "Review"), ("codex", "Review")])

    def test_phase_is_kept_as_written(self):
        factory = self.make_factory("phase", codex=[dict(CODEX_LINES[0], phase="spec gate")])
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        codex = [a for a in data["agents"] if a["provider"] == "codex"]
        self.assertEqual(codex[0]["phase"], "spec gate")

    def test_codex_paths_not_in_html(self):
        row = dict(CODEX_LINES[0], cd="/marker/cd-path", out="/marker/out-path", hint="marker-hint")
        factory = self.make_factory("paths", codex=[row])
        done, data = self.run_ledger(factory)
        self.assertEqual(done.returncode, 0, done.stderr)
        page = self.page(factory)
        for marker in ("cd-path", "out-path", "marker-hint"):
            self.assertNotIn(marker, page)
        self.assertNotIn("marker", json.dumps(data))

    def test_luna_cached_price(self):
        sys.path.insert(0, os.path.dirname(LEDGER))
        import ledger
        got = ledger.codex_cost("gpt-6-luna", 10, 4, 5)
        self.assertAlmostEqual(got, (6 * 0.10 + 4 * 0.01 + 5 * 0.50) / 1e6, places=9)

    def test_sol_cached_bills_at_input_price(self):
        sys.path.insert(0, os.path.dirname(LEDGER))
        import ledger
        got = ledger.codex_cost("gpt-6.1-sol", 10, 4, 5)
        self.assertAlmostEqual(got, (10 * 2.0 + 5 * 10.0) / 1e6, places=9)


if __name__ == "__main__":
    unittest.main()
