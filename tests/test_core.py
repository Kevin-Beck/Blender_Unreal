"""Plain-Python tests for the shared core. Run: python -m unittest discover tests"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import changeset, manifest, renames  # noqa: E402
from core.profile import PROFILE_ID  # noqa: E402


def make_manifest():
    return {
        "schema": 1, "asset_guid": "rig", "profile": PROFILE_ID,
        "skeleton": {"unreal_path": "/Game/K/SKEL_K", "root_bone_id": "b-1", "bones": [
            {"id": "b-1", "name": "root", "parent": None, "rest_hash": "r1"},
            {"id": "b-2", "name": "spine1", "parent": "b-1", "rest_hash": "r2"},
            {"id": "b-3", "name": "head", "parent": "b-2", "rest_hash": "r3"},
        ]},
        "meshes": [{"group_id": "g-1", "asset_guid": "m", "unreal_path": "/Game/K/SK_K", "geometry_hash": "g",
                    "material_slots": [{"id": "m-1", "slot_name": "Cloth", "assigned": ""}]}],
        "sockets": [{"id": "s-1", "name": "Hat", "bone_id": "b-3",
                     "location": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}],
        "physics": {"unreal_path": "/Game/K/PHYS_K", "mode": "CREATE_ON_FIRST"},
        "actions": [{"id": "a-1", "name": "Run", "asset_guid": "a", "unreal_path": "/Game/K/Anims/A_K_Run",
                     "frame_start": 1, "frame_end": 24, "root_motion": True, "keys_hash": "k"}],
    }


def bone(m, bid):
    return next(b for b in m["skeleton"]["bones"] if b["id"] == bid)


class ChangeSetTests(unittest.TestCase):
    def setUp(self):
        self.prev = make_manifest()
        self.cur = copy.deepcopy(self.prev)

    def test_first_import_is_all_new_and_safe(self):
        cs = changeset.compute(None, self.cur)
        self.assertTrue(cs.first_import)
        self.assertFalse(cs.blocked or cs.destructive or cs.remaps)

    def test_unchanged(self):
        self.assertTrue(changeset.compute(self.prev, self.cur).is_empty())

    def test_rename_is_remap_not_delete_add(self):
        bone(self.cur, "b-2")["name"] = "spine_01"
        cs = changeset.compute(self.prev, self.cur)
        self.assertEqual(cs.renames("bone"), {"spine1": "spine_01"})
        self.assertFalse(cs.destructive)

    def test_remove_bone_is_destructive(self):
        self.cur["skeleton"]["bones"].pop()
        self.cur["sockets"] = []
        cs = changeset.compute(self.prev, self.cur)
        self.assertEqual(cs.removed("bone"), ["head"])
        self.assertEqual(cs.removed("socket"), ["Hat"])

    def test_reparent_is_blocked(self):
        bone(self.cur, "b-3")["parent"] = "b-1"
        cs = changeset.compute(self.prev, self.cur)
        self.assertEqual([c.id for c in cs.blocked], ["b-3"])

    def test_new_bone_colliding_with_orphan_is_blocked(self):
        self.prev["skeleton"]["bones"].append(
            {"id": "b-9", "name": "twist", "parent": "b-1", "rest_hash": "x", "orphaned": True})
        self.cur["skeleton"]["bones"].append({"id": "b-10", "name": "twist", "parent": "b-2", "rest_hash": "x"})
        cs = changeset.compute(self.prev, self.cur)
        self.assertEqual([c.id for c in cs.blocked], ["b-10"])

    def test_unchanged_action_is_not_exported(self):
        cs = changeset.compute(self.prev, self.cur)
        self.assertFalse(cs.get("action", "a-1").flags["export"])
        self.cur["actions"][0]["keys_hash"] = "k2"
        cs = changeset.compute(self.prev, self.cur)
        self.assertTrue(cs.get("action", "a-1").flags["export"])


class RenameOrderTests(unittest.TestCase):
    def check(self, mapping, names):
        steps = renames.order_renames(mapping)
        result = renames.apply_to_names(names, steps)
        expected = {mapping.get(n, n) for n in names}
        self.assertEqual(result, expected)
        return steps

    def test_swap_uses_temp(self):
        steps = self.check({"a": "b", "b": "a"}, {"a", "b", "c"})
        self.assertEqual(len(steps), 3)
        self.assertTrue(any(d.startswith(renames.TMP_PREFIX) for _, d in steps))

    def test_chain(self):
        self.check({"a": "b", "b": "c", "c": "d"}, {"a", "b", "c"})

    def test_three_cycle_and_independent(self):
        self.check({"a": "b", "b": "c", "c": "a", "x": "y"}, {"a", "b", "c", "x"})


class ManifestTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(manifest.validate(make_manifest()), [])

    def test_two_roots(self):
        m = make_manifest()
        bone(m, "b-2")["parent"] = None
        self.assertTrue(any("root" in e for e in manifest.validate(m)))


if __name__ == "__main__":
    unittest.main()
