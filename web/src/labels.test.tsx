import { describeReason } from "./labels";

test.each([
  ["prohibited:minor@frame=57", true, "Looks under 18 at 0:57", "block"],
  ["review:real_person@frame=3", undefined, "Possible real person · frame 3", "hold"],
  ["review:self_harm@frame=0", false, "Possible self-harm", "hold"],
  ["explicit_unverified", undefined, "Explicit, workspace not verified", "block"],
  ["screening_unavailable:timeout", undefined, "Not screened yet: provider timed out", "info"],
  ["reviewed:approved_by=admin_u", undefined, "Approved by admin_u", "plain"],
  ["prompt:minor", undefined, "Prompt: looks under 18", "block"],
  ["something_new", undefined, "something_new", "plain"],
])("%s", (reason, isVideo, text, tone) => {
  expect(describeReason(reason, isVideo as boolean | undefined)).toEqual({ text, tone });
});
