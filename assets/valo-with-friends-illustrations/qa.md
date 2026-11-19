# QA record

Applied the skill's five references: style DNA, Xiaohei IP, composition patterns, prompt template, and QA checklist. English labels replace Chinese as requested. Prior example images/compositions were not used as generation references.

## Shared checks

- **Layout:** all are horizontal, approximately 16:9. Native dimensions are 1672 × 941 (ratio 1.776833 versus exact 16:9 = 1.777778; a 0.053% difference). This native output rounding is retained, not silently described as exact 16:9.
- **Background:** visually clean neutral white, with no colored ground, paper backdrop, gradients, or cast shadows. A sampled empty corner varies from RGB 253 to 255 due to faint generated pixel variation; these are not flat-white vector files.
- **Drawing:** black, irregular hand-drawn lines with sparse accents. Orange indicates motion, red emphasizes a result, blue annotates supporting context. Some objects have sparse construction/hatch strokes.
- **Character:** solid black, small white dot eyes, thin limbs, deadpan expression. Every scene depends on Xiaohei performing its core action.
- **Density:** one principal idea per image, substantial blank surrounding space, at least approximately 35% visually quiet area; no paragraph-length text, slide heading, node grid, or architecture sprawl.
- **Text:** manually inspected every label; readable English, no spelling failure. Labels remain short. Tiny generic tally marks and player symbols do not reproduce actual player data.
- **Originality:** new repository-specific metaphors; none reuses the prohibited example layouts.
- **Content:** no implication that a live Riot adapter exists, no invented measured performance statistics, no claim that v4 is active or approved.
- **Files:** all six final copies verified by SHA-256 equality with their original generated files. All PNGs open successfully.
- **Git:** no add/commit actions. Existing untracked review documents were left alone. Only the new illustration directory was added.

## Per-image visual review

| Image | Action and meaning | Text/color/composition | Selection |
|---|---|---|---|
| 01 | Sewing actively turns loose match slips into a shared scrapbook. | Three main annotations; orange needle movement and red result; white margins. The thread crossing the character is part of the sewing action. | Pass; strong opener |
| 02 | Xiaohei drives the adapter press; two wrappers lead to one schema. | Four readable annotations; low-tech object rather than a formal architecture graphic. | Pass |
| 03 | Xiaohei physically supports and adjusts the contextual balance. | Equal kill tokens, distinct state labels, emphasized Impact. No quantitative scoring assertion. | Pass; strongest conceptual sketch |
| 04 | A shared focusing knob and specimen make the benchmark comparison concrete. | Correct two circles/two squares; You/Teammates share 2v2 and side; no fabricated number. | Pass; use beside the metric explanation |
| 05 first generation | Folding and filing conveyed caching. | History strip ran into the left edge; composition lacked the desired outer breathing room. | Reject |
| 05 revised generation | Folding history into cached Recent/Career views remains central. | Paper end is fully visible; wide white margins, four readable labels. | Pass; selected final |
| 06 | Xiaohei produces two carbon copies from one pinned setup. | Both impressions show a diamond and three tally strokes; review and runtime labels are correct. | Pass |

## Iteration

Generated seven images total using individual built-in image_gen calls. For 05, regenerated with a centered scene, a finite five-fold history strip, and explicit side margins. Inspected the revised image before selection.

All original outputs, including rejected 05, remain in the generator output directory. `prompts.json` records all final prompts, the rejected prompt, source paths, and its rejection reason.
