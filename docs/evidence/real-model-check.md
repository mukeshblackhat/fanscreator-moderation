# Real model check: public images and videos from Wikimedia Commons

Run on 29 Oct 2026 on macOS (Apple silicon), `docker compose` with the `real` profile. Primary scorer: `Falconsai/nsfw_image_detection` on CPU. OpenAI secondary: off (no key). Files were fetched from Wikimedia Commons with its API, uploaded through `POST /media/upload` and screened by the normal pipeline. The scores below are read from the `scores` table.

What I chose not to test with: pornographic content scraped from the web (there is no way to check ages or consent, which is the one thing a moderation team can never be casual about), swimwear or beach photos (they can include children), and photos of real public figures. Testing the explicit category on photographic adult content needs a licensed, curated dataset.

## Images

| Kind | File on Commons | Licence | Workspace | Max explicit | Result |
| --- | --- | --- | --- | --- | --- |
| Everyday | Callie the golden retriever puppy.jpg | CC BY 3.0 | unverified | 0.0002 | published |
| Everyday | Champ de Mars from the Eiffel Tower - July 2006 edit.jpg | CC BY 2.5 | unverified | 0.0001 | published |
| Everyday | Margherita Originale.JPG | CC BY-SA 3.0 | unverified | 0.0001 | published |
| Everyday | Times Square 20190726.jpg | CC BY-SA 4.0 | unverified | 0.0002 | published |
| Everyday | Oriental shorthair kitten.jpg | CC BY-SA 4.0 | unverified | 0.0002 | published |
| Painting | Mona Lisa, by Leonardo da Vinci, from C2RMF retouched.jpg | Public domain | unverified | 0.0001 | published |
| Artistic nudity | La nascita di Venere (Botticelli).jpg | Public domain | unverified | 0.0002 | published |
| Artistic nudity | Edouard Manet - Olympia - Google Art Project 3.jpg | Public domain | unverified | 0.0015 | published |
| Artistic nudity | Michelangelo's David 2015.jpg | CC BY-SA 4.0 | unverified | 0.0004 | published |
| Artistic nudity | Tiziano - Venere di Urbino - Google Art Project.jpg | Public domain | unverified | **0.8310** | **blocked** (`explicit_unverified`) |
| Artistic nudity | Tiziano - Venere di Urbino - Google Art Project.jpg | Public domain | verified | 0.8310 | published (explicit allowed) |
| (search miss) | Tête de la grande odalisque couchée (Ingres), head detail only | CC BY-SA 4.0 | unverified | 0.0005 | published, not a nudity test |

## Videos (1 frame per second)

| Video | Frames | Max explicit (frame) | Result |
| --- | --- | --- | --- |
| Sintel trailer-1080p.ogv (CC BY 3.0, Blender Foundation), first 52 s | 52 | 0.1421 (frame 35) | published |
| Timelapse clouds.ogv (CC BY-SA 3.0), 16 s | 16 | 0.0002 | published |
| Spliced: 55 s of the puppy photo, then 3 s of the Titian painting, unverified | 58 | frames 0 to 54 all < 0.01; **frames 55, 56, 57 = 0.9696** | **blocked** (`explicit_unverified`) |
| Same spliced video, verified | 58 | same | published |

## What this shows

- **No false alarms on everyday photos.** All six scored 0.0002 or less, far below the 0.5 hold threshold.
- **Every frame really counts on a real video.** In the spliced clip, the only frames over 0.01 are the three seconds of the painting at the very end, which blocked the whole video in the unverified workspace. This is the incident's second bug (late frames ignored), now checked with a real model on real pixels.
- **The local model is lenient with classical art.** Of four artistic nudes it flagged only Titian's Venus of Urbino. It is trained to separate photographic pornography from normal images, so paintings and statues mostly pass. Whether art should count as `explicit` is a policy decision; if it should, this model is not enough on its own. The OpenAI secondary (when a key is set) or a second model trained on art would be needed.
- **Speed on CPU:** about 0.2 to 1.3 s per image and about 0.15 to 0.45 s per video frame, including the callback.

## Not shown here

- True positive rate on photographic explicit content (needs a licensed dataset, see above).
- Self-harm and sexual violence on real files (need `OPENAI_API_KEY`).
- Age, real person and animals (not checked on real files at all; use the synthetic tests).
