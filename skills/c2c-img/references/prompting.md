# Writing image prompts

Codex passes your prompt to the image model exactly as written, so the result is only as good
as your art direction. Write the prompt in English. The exception is any text that must appear
in the image: quote it exactly, in its own language.

## How much to add

- **The user was specific:** keep every detail. Put it in order and structure it, but don't
  add creative ideas of your own.
- **The user was vague**, as in "画只猫" or "a hero image for my site": add tasteful direction
  where it materially helps, such as medium, composition, lighting, palette, and intended use.
  A vague request should still come back looking deliberate and polished.
- **Never add** characters, objects, brands, slogans, or text that the request doesn't imply.
- For a vague request, take the obvious reading. Don't ask the user which one they meant.

## Structure

Use only the lines that help:

```text
Use case: <photo | product-mockup | illustration | logo-brand | ui-mockup | infographic | poster | sticker | edit ...>
Asset type: <where it will be used: landing hero, app icon, book cover, social post ...>
Primary request: <one sentence: the core ask>
Input images: <Image 1: edit target; Image 2: style reference>          (only with -i)
Scene/backdrop: <environment>
Subject: <main subject, with concrete details>
Style/medium: <photoreal 50mm | flat vector | watercolor | 3D clay render | ink wash ...>
Composition: <framing + placement, e.g. "subject on right third, empty sky top-left for a title">
Lighting/mood: <golden hour, soft studio, moody low-key ...>
Color palette: <2-4 colors or a feel>
Text (verbatim): "<exact text>" — <font feel>, <placement>
Constraints: <must keep / must avoid>; no watermark
```

Scene first, then the subject, then details, then constraints. Use camera language for photos
(lens, depth of field, angle), and name the medium for illustration.

## Choosing aspect ratio and size by context

| Destination | Ratio | Suggested `-s` |
|---|---|---|
| Avatar, icon, social square, sticker | 1:1 | `1024x1024`, or `512x512` for an icon |
| Blog header, OG or social share card | ~1.91:1 | `1200x630` |
| Website hero, slide, video thumbnail | 16:9 | `1920x1080` or `1280x720` |
| Phone wallpaper, story, reel cover | 9:16 | `1080x1920` |
| Poster, flyer, book cover | 3:4 or 2:3 | `1200x1600` |
| Product shot, portrait photo | 4:5 | `1080x1350` |

Without `-s`, write the ratio into the Composition line, for example "wide 16:9 landscape". The
model's native output is around 1–1.7 MP. Upscaling well beyond that (to 4K, for instance)
looks soft; the script adds a note when it upscales.

## Text in images

- Keep in-image text short, since headlines work better than paragraphs. Quote it exactly.
  Chinese, Japanese, and Korean text works well.
- Say where it goes, how big it is, and what it feels like, for example "large brush-calligraphy
  title centered in the upper third".
- For unusual words or brand names, spell them out letter by letter and ask for a verbatim
  rendering.
- If the user didn't specify any text, prefer none ("no text"). Don't invent copy.
- Always check the text when you review the image. Garbled text is the most common defect worth
  a retry.

## Recipes

**Photo or lifestyle scene.** Give a real place, real textures, and natural light. Mention the
lens and depth of field. "candid, editorial" keeps it from looking like stock.

**Product shot.** Give the surface, the backdrop color, and the lighting setup (softbox, rim
light). Say "no logos" unless the user gave one. Leave negative space if it's for a page.

**Illustration or concept art.** Name the medium and its texture, the palette, and the level of
detail. For a series, reuse the same style line word for word so the images match.

**Logo or icon concept.** Ask for simple bold shapes that read at small sizes, a limited
palette, and a centered mark with padding. Add `-t` for a transparent background. It's a
concept: mention that a production logo should be redrawn as a vector.

**Poster or cover.** Put the scene and mood first. Give the text lines verbatim with their
hierarchy (title, then subtitle, then date), and leave clear space for the text.

**UI mockup.** Give the fidelity (wireframe or polished), the platform and screen size, and
the key components. Keep text minimal and real. If the user needs working UI, build it as code
instead.

**Photo restyle (cartoon, pixel art, anime, watercolor, and so on).** Pass the photo with
`-i` and describe the whole redraw. Keep the people recognizable:

```text
Use case: style-transfer
Primary request: Edit Image 1 — redraw the entire photo as <style>.
Keep: the same people with recognizable faces, expressions, hairstyles and outfits; the same pose,
framing and background layout.
Style: <e.g. pixel art — visible square pixels, limited ~32-color palette, clean dark 1-px
outlines, 16-bit retro game look | anime — cel shading, clean line art, soft pastel light>
Constraints: no text, no watermark
```

**Sticker or transparent cutout.** Use `-t` and write "isolated on a fully transparent
background". Mention an outline ("thick white die-cut border") for stickers. Check the edges
when you review.

## Edits (`-i`)

- Start with `Edit Image 1:` and then **what changes**. Follow with **what stays**:
  identity, face, pose, framing, colors, text, and background, as relevant.
- Change one thing per round. Repeat the unchanged list word for word on every iteration, so
  details don't drift over several rounds.
- For a style reference, write: `Use Image 2 only as a style reference (palette, brushwork,
  lighting); do not copy its subject or layout.`
- For compositing, write: `Place the product from Image 2 onto the table in Image 1; match
  perspective, lighting and shadows.`
- To change the background or remove it: `Edit Image 1: replace only the background with …;
  keep the subject's edges, colors and lighting unchanged.`, adding `-t` to remove it.

## Always avoid

Watermarks and signatures, extra text or logos nobody asked for, cluttered compositions,
imitating a named living artist's style, and realistic images of real, identifiable people in
fabricated situations.
