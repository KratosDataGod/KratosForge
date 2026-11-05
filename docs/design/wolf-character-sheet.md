# The KratosForge wolf: character sheet

One wolf, everywhere. This sheet fixes the design so that every new image is the same animal.
The approved painted references are the storyboard panels; the approved silhouette is the brand
emblem. Rules for using the wolf are in the
[visual style guide](KRATOSFORGE_VISUAL_STYLE_GUIDE.md#the-kratosforge-wolf).

## Design

| Trait | Specification |
| --- | --- |
| Build | Large, heavy-shouldered northern wolf. Long legs, deep chest, thick neck ruff. Believable anatomy, heroic proportions (about 10% more mass in the shoulders and ruff than a real wolf). |
| Fur | Dark blue-black (`#0B1420` into `#1B3664`), steel-blue highlights (`#244A70`) on the ruff, back and tail, warm white (`#F3F5F7`) only on the muzzle, chest and the inside of the ears. Ink lines in the fur direction, painted light on top. |
| Eyes | Amber-orange (`#F8991C`), a single warm highlight, dark rim. The eyes are the brightest thing in a calm image. |
| Markings | A darker mask across the eyes, a pale blaze down the chest. No spots, no collar, no scars. |
| Light | When the forge is in the scene, orange forge light on the muzzle, chest and the near legs; cold blue rim light from the opposite side. |
| Expression | Calm and intelligent at rest. Determined in motion. The mouth opens for the howl and the snarl only. Never a grin, a wink, a tongue out or a raised eyebrow. |
| Line | Bold black ink on the silhouette, finer ink inside, crosshatch in the shadows. |

### Silhouette check

At 32 px the emblem reads as head, ears and ruff. At any size the wolf is recognizable by the
heavy ruff, the long muzzle and the upright ears set wide. A new image whose silhouette does not
match the emblem's is rejected.

## The eight reference poses

| # | Pose | Used for | Framing | Reference today | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | Portrait | Launch, SQL, the calm state, profile | Head and shoulders, three-quarter, looking just past the camera | Storyboard panels 1 and 4 | placeholder, needs a full-size original |
| 2 | Running | Notebook execution, navigation | Full body, mid-stride, left to right, fire and embers trailing | Storyboard panels 2 and 9 | placeholder, needs a full-size original |
| 3 | Howling | Achievements, the Titan | Head back, three-quarter from below, breath or frost | Storyboard panel 7 | placeholder, needs a full-size original |
| 4 | Emerging from forge energy | Launch at Expressive, onboarding | Chest and head pushing out of orange paint and sparks, head low | none | needed |
| 5 | Celebrating | Badges below the Titan, streaks | Standing tall on a rise, chest out, one forepaw raised, ember trail | none | needed |
| 6 | Warning | Warnings that are not failures | Ears back, head low, eyes on the viewer, cold blue, no red | none | needed |
| 7 | Minimal silhouette | Favicon, tray, 16 to 32 px | Head and ruff in one shape | `docs/brand/wolf-forge-emblem-32.png` | approved |
| 8 | Monochrome ink | Print, docs on light backgrounds, watermark | Portrait in black ink only, crosshatched | none | needed |

The snarling wolf in storyboard panel 8 is the error treatment. It is a variant of the warning
pose with the mouth open and restrained crimson light; it is still, never animated, and it is the
only wolf that may appear with red.

## Delivery specification

- PNG, transparent background (or flat `#0B1420` to key), at least 2000 px on the long edge.
- Same scale across poses 1 to 6 so the head is the same size in each.
- Light from the left (forge) and a cold rim from the right, so poses composite beside UI on the
  right as the storyboard does.
- One master file per pose under `docs/design/art/wolf/`, shipping WebP under `app/ui/art/`, each
  recorded in the [asset manifest](assets.md).

## Generation prompt

Use the standard instruction from the
[visual style guide](KRATOSFORGE_VISUAL_STYLE_GUIDE.md#generating-artwork), then:

> Subject: the KratosForge wolf, a large heavy-shouldered northern wolf with dark blue-black fur,
> steel-blue highlights on the ruff and back, warm white on the muzzle and chest, amber-orange
> eyes with a single highlight, a dark mask across the eyes, bold black ink outline and crosshatched
> shadows, painted forge light from the left and a cold blue rim light from the right.
> Pose: {one of the eight above}. Framing: {per the table}. Background: transparent. No text, no
> collar, no logo, one wolf only.

Attach `docs/design/reference/kratosforge-storyboard.png` and, once it exists, the approved
portrait, to every request. If the generator produces a wolf with a different mask, eye colour,
ear set or ruff, regenerate; do not retouch it into compliance.
