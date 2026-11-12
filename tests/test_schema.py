import json
import os
import unittest

PATH = os.path.join(os.path.dirname(__file__), "..", "skills", "software-factory", "references", "bugs.schema.json")


def load():
    with open(PATH) as f:
        return json.load(f)


def walk(node, path="$"):
    """Yield (path, node) for every dict in the schema."""
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from walk(v, path + "." + k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, "%s[%d]" % (path, i))


def resolve(schema, ref):
    node = schema
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def refs(schema):
    return [(p, n["$ref"]) for p, n in walk(schema) if "$ref" in n]


def objects(schema):
    return [(p, n) for p, n in walk(schema) if n.get("type") == "object" and "properties" in n]


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.schema = load()

    def bugs(self):
        return self.schema["$defs"]["BUGS"]

    def test_every_object_is_strict(self):
        objs = objects(self.schema)
        self.assertGreaterEqual(len(objs), 3)
        for p, n in objs:
            self.assertIs(n["additionalProperties"], False, p)

    def test_every_object_requires_all_properties(self):
        for p, n in objects(self.schema):
            self.assertEqual(set(n["required"]), set(n["properties"]), p)

    def test_defs_hold_only_bugs(self):
        self.assertEqual(sorted(self.schema["$defs"]), ["BUGS"])
        self.assertTrue(any(".$defs.BUGS" in p for p, _ in objects(self.schema)))

    def test_root_is_ref_to_bugs(self):
        self.assertEqual(self.schema["$ref"], "#/$defs/BUGS")
        self.assertEqual(self.schema["type"], "object")
        self.assertIs(resolve(self.schema, self.schema["$ref"]), self.bugs())

    def test_every_ref_resolves_and_none_is_root(self):
        found = refs(self.schema)
        self.assertGreaterEqual(len(found), 1)
        for path, ref in found:
            self.assertNotEqual(ref, "#", path)
            self.assertIsInstance(resolve(self.schema, ref), dict, path)

    def test_root_keys(self):
        self.assertEqual(set(self.schema),
                         {"$schema", "title", "description", "type", "$ref", "$defs"})

    def test_top_level_keys(self):
        want = {"provider", "model", "base", "reviewed_head", "target_ok", "scope", "bugs", "answers"}
        self.assertEqual(set(self.bugs()["properties"]), want)
        self.assertEqual(set(self.bugs()["required"]), want)
        self.assertNotIn("ran", self.bugs()["properties"])
        self.assertEqual(self.bugs()["properties"]["target_ok"]["type"], "boolean")
        for k in ("base", "reviewed_head"):
            self.assertEqual(self.bugs()["properties"][k]["type"], ["string", "null"])

    def test_scope_needs_a_checked_entry(self):
        scope = self.bugs()["properties"]["scope"]
        self.assertEqual(set(scope["properties"]), {"checked", "unchecked"})
        self.assertEqual(scope["properties"]["checked"]["minItems"], 1)

    def test_bug_keys(self):
        bug = self.bugs()["properties"]["bugs"]["items"]
        self.assertEqual(set(bug["properties"]),
                         {"id", "title", "severity", "repro", "expected", "actual", "evidence", "files", "verdict"})

    def test_every_bug_has_a_string_id(self):
        bug = self.bugs()["properties"]["bugs"]["items"]
        self.assertEqual(bug["properties"]["id"], {"type": "string"})
        self.assertIn("id", bug["required"])

    def test_answers_carry_id_status_and_evidence(self):
        answers = self.bugs()["properties"]["answers"]
        self.assertEqual(answers["type"], "array")
        item = answers["items"]
        self.assertEqual(set(item["properties"]), {"id", "status", "evidence"})
        self.assertEqual(item["properties"]["status"]["enum"], ["fixed", "open", "rejected"])
        self.assertIn("answers", self.bugs()["required"])

    def test_severity_enum(self):
        bug = self.bugs()["properties"]["bugs"]["items"]
        self.assertEqual(bug["properties"]["severity"]["enum"], ["critical", "high", "medium", "low"])

    def test_verdict_enum_has_null(self):
        v = self.bugs()["properties"]["bugs"]["items"]["properties"]["verdict"]
        self.assertIn(None, v["enum"])
        self.assertEqual(set(v["enum"]), {"confirmed", "rejected", "unverified", None})
        self.assertIn("null", v["type"])

    def test_description_names_verdict_rule(self):
        d = self.schema["description"]
        self.assertIn("leaves verdict null", d)
        self.assertIn("The lead sets it", d)
        self.assertIn("Spec gate", d)

    def test_description_names_the_follow_up_rule(self):
        d = self.schema["description"]
        self.assertIn("answers", d)
        self.assertIn("follow-up", d)


if __name__ == "__main__":
    unittest.main()
