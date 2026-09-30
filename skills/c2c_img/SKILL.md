---
name: c2c_img
description: Built-in image generation and editing for Claude, powered by the user's local Codex (ChatGPT) image_gen tool, with no API key needed. Use it whenever the user wants a picture made or changed, such as a request to draw, generate, create, render or design an image, photo, illustration, poster, banner, cover, wallpaper, avatar, icon, logo concept, sticker, product shot, mockup, meme or transparent cutout, or to edit, retouch or restyle an image, change or remove its background, or make it more X. Also use it when they say 画/画一张/生成图片/生图/出图/做张海报/配图/头像/壁纸/改图/P图/抠图/换背景/再来一张. Use it proactively when a task needs a bitmap asset (hero image, placeholder photo, game sprite), even if the user never mentions Codex or this skill. Don't use it for charts, diagrams, flowcharts or UI that should be built as code or SVG.
---

# c2c_img: image generation that feels built in

To the user, you can simply make images. Behind the scenes, `scripts/c2c_img.py` has Codex's
`image_gen` render your prompt exactly as written, and you do the art direction. **The user should
feel that they asked for an image and got one.** Don't mention Codex, scripts, JSON, or your
prompt unless they ask or something breaks.

## The loop

1. **Start right away.** Don't ask clarifying questions for ordinary requests. Choose sensible
   defaults for aspect ratio, style, and composition from context, the way a good designer
   would. Ask only when a missing detail would make the result useless, such as the exact
   wording for a poster headline the user clearly cares about.
2. **Write the prompt yourself** and don't show it to the user. Follow
   [references/prompting.md](references/prompting.md). Read it the first time you use this
   skill in a conversation.
3. **Generate** by running a single Bash call with `timeout: 600000`. Set the `description` to
   plain language, such as `Generating image: red fox in snow` or `正在生成图片：中秋海报`.
   That line is what the user sees while they wait.
4. **Check the result quietly** by opening each image with the Read tool. If it has an obvious
   defect, fix it once before showing anything. Obvious defects are garbled or misspelled
   text, a missing required element, the wrong aspect ratio, or a watermark. Don't iterate on
   matters of taste.
5. **Show it.** If a tool for showing files to the user exists (such as `SendUserFile` with
   `display: "render"`), send the image or images so they appear inline. Otherwise, link the
   path. Add one short sentence about what you made. Offer one natural next step only when it
   helps, such as "Want a warmer palette or a portrait version?". Reply in the user's language.
6. **Keep the thread going.** Remember the paths you've produced. For requests like "make it
   brighter", "换成蓝色", or "remove the cup", edit the latest image with `-i <last path>` and
   restate what must stay the same. The script never overwrites files: it saves `-v2`, `-v3`,
   and so on automatically.

## Running the script

```bash
python3 <skill_dir>/scripts/c2c_img.py [options] "<prompt>"
```

`<skill_dir>` is the base directory shown when this skill loaded. On Windows, use `python` or
`py -3` if `python3` isn't available.

| Option | Use |
|---|---|
| *(no `-o`)* | For preview or brainstorming images. They are saved to `~/Pictures/c2c_img/<time>-<slug>.png`, which keeps the user's repo clean |
| `-o PATH` | For project assets. Put them where the project keeps images (for example `public/`, `assets/`, `static/img/`) and give them a descriptive name. The extension sets the format |
| `-s WxH` | Exact pixel size. The image is generated at the nearest aspect ratio, then scaled and center-cropped. Use it for icons, banners, and social cards |
| `-f png\|jpg\|webp`, `-q 90` | Output format and quality. Use jpg or webp for web photos and png for anything with transparency or crisp text |
| `-t` | Transparent background. Also write "isolated on a fully transparent background" in the prompt |
| `-i IMG` (repeatable) | An input image to edit or use as a reference. In the prompt, refer to them as Image 1, Image 2, … in the order given |
| `-n N` | N variants of the **same** prompt, run in parallel. Only use it when the user wants options |
| `--batch -` | Several **different** images in one call, run in parallel. Pass JSON lines on stdin, one per image: `{"prompt", "out"?, "ref"?, "transparent"?, "size"?, "format"?}` |
| `--doctor` | Checks the setup without generating anything. Run it when something fails for an unclear reason |

Timing: a new image takes about 35–50 s, and an edit or a transparent image about 45–75 s. The
server side varies from minute to minute. Parallel jobs finish in about the time of one job. For a batch of 3 or more images, tell the user
in one short line that they're on the way (for example "Making 4 images, about a minute."). Run
the batch with `run_in_background: true` if you have other work to do meanwhile.

Output on stdout is JSON. Progress goes to stderr.

```json
{"ok": true, "images": ["/abs/a.png"], "results": [{"ok": true, "path": "/abs/a.png", "width": 1254, "height": 1254, "notes": [...]}]}
```

A failed job has `error_type`, `error`, and `hint` fields. Act on `hint`. When you explain the
problem to the user, keep it short and non-technical.

| `error_type` | What to do |
|---|---|
| `generation_failed` | Usually a content-policy refusal. Rephrase within the policy, or tell the user plainly why it can't be made. Never try to evade the policy |
| `quota` | The ChatGPT/Codex usage limit has been reached. Tell the user, including when it resets (`resets_at`) if that's given, and don't retry |
| `capacity` | OpenAI is overloaded, and another model was already tried. Wait a minute, retry once, then report it |
| `auth` | The user needs to run `codex login` with their ChatGPT account |
| `timeout` | Retry once, then report it |
| `bad_request` | Fix the arguments and retry. For example, if webp isn't available here, use png |
| `model` / `unknown` | Run `--doctor` and report what it shows. `--no-daemon` forces the simpler engine, and `--stop-daemon` restarts the background helper |

## Inputs from the user

- **An image path or dragged-in file:** pass it with `-i`. Say whether it's the thing to edit
  or only a style reference.
- **An image pasted into the chat with no file path:** you can see it, but the script needs a
  file. Ask the user, in one line, to drag the file in or give its path. If you have enough to
  describe it well, you can also describe it in the prompt and generate from scratch.

## Judgment

- Choose `-s` whenever the destination implies a size, such as a favicon, app icon, OG card,
  or banner.
- For images that go into a project, update whatever references them (HTML, CSS, markdown) if
  that is part of the task.
- Each image uses the user's ChatGPT/Codex quota. Make what was asked for. Don't generate
  speculative variants unless the user wants options.
- Anything that should be precise and editable, such as a chart, a diagram, a UI, or a simple
  geometric icon, is better built as code or SVG. Offer that instead.
