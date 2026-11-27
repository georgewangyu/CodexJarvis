import copy
import importlib.util
from pathlib import Path
import unittest

import task_parking as p


def section(sid, name, ids):
    return {"sectionId": sid, "name": name, "itemKeys": ["codex:thread:local:" + t for t in ids]}


class ParkingTests(unittest.TestCase):
    def setUp(self):
        self.inventory = {"sections": [section("work", "Work", ["done", "pending"]),
                         section("refs", "References", ["old"])], "pinnedThreads": []}
        self.read = {"thread": {"id": "done", "status": {"type": "idle"}}, "turns": [
            {"id": "turn", "status": "completed", "error": None, "completedAt": 200,
             "items": [{"type": "agentMessage", "phase": "final_answer", "text": "Delivered. Want optional polish?"}]}]}
        self.assessment = dict(turn_id="turn", objective_complete=True, return_contract_fulfilled=True,
            ordinary_nonrecurring=True, required_gate_pending=False, needs_input=False,
            unfinished_backlog=False, ambiguous=False, optional_future_offer=True,
            reason="Requested deliverable complete; polish not requested.", result_pointer="codex://threads/done")
        self.bundle = dict(read=self.read, current_read=copy.deepcopy(self.read), assessment=self.assessment,
                           inventory=self.inventory, source_section="work")

    def test_optional_future_offer_does_not_hold_completed_objective(self):
        self.assertTrue(p.eligible(self.bundle)["eligible"])

    def test_completed_repair_after_human_confirmation_is_eligible_not_merely_idle(self):
        for read in (self.bundle['read'], self.bundle['current_read']):
            read['turns'][0]['items'][0]['text'] = 'Hide/show repaired the surface; human confirmed it stayed fixed after showing the app.'
        self.assessment.update(reason='Actual one-off repair and verification fulfilled; apps preserved.',
                               result_pointer='codex://threads/done/turn')
        self.assertTrue(p.eligible(self.bundle)['eligible'])

    def test_duplicate_notice_after_verified_parking_does_not_move_again(self):
        self.inventory['sections'][0]['itemKeys'].remove('codex:thread:local:done')
        self.inventory['sections'][1]['itemKeys'].insert(0, 'codex:thread:local:done')
        decision = p.eligible(self.bundle)
        self.assertFalse(decision['eligible'])
        self.assertIn('already parked', decision['reason'])

    def test_each_real_gate_and_unknown_are_preserved(self):
        for key in ["required_gate_pending", "needs_input", "unfinished_backlog", "ambiguous"]:
            for value in [True, None]:
                b = copy.deepcopy(self.bundle); b["assessment"][key] = value
                self.assertFalse(p.eligible(b)["eligible"], (key, value))
        for key in ["objective_complete", "return_contract_fulfilled", "ordinary_nonrecurring"]:
            b = copy.deepcopy(self.bundle); b["assessment"][key] = False
            self.assertFalse(p.eligible(b)["eligible"])

    def test_idle_alone_never_proves_done(self):
        self.bundle["current_read"]["turns"] = []
        self.assertFalse(p.eligible(self.bundle)["eligible"])

    def test_normal_final_required(self):
        for patch in [dict(status="failed"), dict(status="interrupted"), dict(error="bad"),
                      dict(items=[]), dict(completedAt=None), dict(completedAt=True),
                      dict(completedAt='claimed'), dict(completedAt=-1)]:
            b = copy.deepcopy(self.bundle)
            for read in [b["read"], b["current_read"]]: read["turns"][0].update(patch)
            self.assertFalse(p.eligible(b)["eligible"])

    def test_reopened_and_active_tasks_refused(self):
        b = copy.deepcopy(self.bundle); b["current_read"]["turns"][0]["id"] = "new"
        self.assertFalse(p.eligible(b)["eligible"])
        b = copy.deepcopy(self.bundle); b["current_read"]["thread"]["status"] = {"type": "active"}
        self.assertFalse(p.eligible(b)["eligible"])

    def test_changed_final_or_new_user_direction_refused(self):
        self.bundle["current_read"]["turns"][0]["items"].append({"type": "userMessage", "text": "Actually revise it"})
        self.assertFalse(p.eligible(self.bundle)["eligible"])

    def test_runtime_holds_cannot_be_overridden_by_semantic_booleans(self):
        for kind in ["paused", "blocked", "needs_input", "needsAttention", "future_status", None]:
            b = copy.deepcopy(self.bundle)
            b["current_read"]["thread"]["status"] = {"type": kind}
            self.assertFalse(p.eligible(b)["eligible"], kind)

    def test_wrong_identity_and_assessment_binding_refused(self):
        for where, key, value in [("assessment", "turn_id", "old"), ("assessment", "result_pointer", "")]:
            b = copy.deepcopy(self.bundle); b[where][key] = value
            self.assertFalse(p.eligible(b)["eligible"])
        self.bundle["current_read"]["thread"]["id"] = "other"
        self.assertFalse(p.eligible(self.bundle)["eligible"])

    def test_protected_pinned_and_recurring_refused(self):
        self.bundle["protected"] = ["done"]
        self.assertFalse(p.eligible(self.bundle)["eligible"])
        del self.bundle["protected"]
        self.inventory["pinnedThreads"] = [{"id": "done"}]
        self.assertFalse(p.eligible(self.bundle)["eligible"])

    def test_manual_placement_race_refused(self):
        self.inventory["sections"][0]["itemKeys"].remove("codex:thread:local:done")
        self.assertFalse(p.eligible(self.bundle)["eligible"])

    def test_missing_or_ambiguous_references_stops(self):
        for inventory in [{"sections": []}, {"sections": [section("a", "References", []), section("b", "References", [])]}]:
            with self.assertRaises(ValueError): p.plan(inventory)

    def test_membership_sized_budget_covers_realistic_backlog(self):
        self.inventory["sections"][0] = section("work", "Work", [str(i) for i in range(34)])
        a = p.plan(self.inventory)
        self.assertEqual((a["member_count"], a["read_budget"], len(a["review_ids"]), a["revalidation_reserved"]), (34, 68, 34, 34))

    def test_budget_cap_and_no_starvation(self):
        self.inventory["sections"][0] = section("work", "Work", [str(i) for i in range(80)])
        a = p.plan(self.inventory)
        self.assertEqual(a["read_budget"], 120)
        c = p.advance(a, a["review_ids"])
        b = p.plan(self.inventory, {"completion_review_cursor": c})
        self.assertEqual(b["review_ids"][:20], [str(i) for i in range(60, 80)])

    def test_deadline_partial_reads_keep_unchecked_first(self):
        a = p.plan(self.inventory)
        c = p.advance(a, ["done"])
        self.assertEqual(c["remainingQueue"], ["pending", "done"])

    def test_new_members_and_stale_cursor(self):
        a = p.plan(self.inventory, {"completion_review_cursor": {"remainingQueue": ["pending", "gone"]}})
        self.assertEqual(a["review_ids"], ["pending", "done"])
        c = p.advance(a, a["review_ids"], ["done"])
        self.assertEqual(c["remainingQueue"], ["pending"])

    def test_full_orders_preserve_concurrent_members_and_relative_order(self):
        inv = {"sections": [section("work", "Work", ["pending", "new-source"]),
               section("refs", "References", ["old", "done", "new-ref", "old2"])]}
        self.assertEqual(p.orders(inv, ["done"], ["work"]),
                         {"work": ["pending", "new-source"], "refs": ["done", "old", "new-ref", "old2"]})

    def test_orders_refuse_unverified_move_and_mixed_members(self):
        with self.assertRaises(ValueError): p.orders(self.inventory, ["done"], ["work"])
        self.inventory["sections"][0] = section("work", "Work", ["pending"])
        self.inventory["sections"][1]["itemKeys"].extend(["codex:thread:local:done", "chatgpt:conversation:other"])
        with self.assertRaisesRegex(ValueError, "mixed/remote/project"):
            p.orders(self.inventory, ["done"], ["work"])

    def test_orders_refuse_duplicate_cross_section_placement(self):
        self.inventory["sections"][1]["itemKeys"].append("codex:thread:local:done")
        with self.assertRaises(ValueError): p.orders(self.inventory, ["done"], ["work"])


if __name__ == "__main__":
    unittest.main()
