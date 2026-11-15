# Mission Steps: A Plain-Language Guide

This guide explains every page in the **Mission steps** menu of Lunar Landing Navigator. You do not need any technical or space background to follow it.

## The big idea

You pick a spot near the Moon's south pole and a date. The app then tells you three things:

- **Sunlight**: when the Sun shines on that spot, so solar panels can charge.
- **Earth in view**: when Earth is above the horizon, so the spot could talk to Earth directly.
- **Darkness**: how long the spot stays in shadow, so batteries have to do the work.

The south pole is full of mountains and craters. A tall ridge can hide the Sun or Earth even when they are up in the sky. The app works this out from real NASA maps of the Moon's surface and real positions of the Sun and Earth.

## How the screen is laid out

- **The big 3D view** fills most of the screen. It shows Earth, the Moon and a spacecraft flying between them.
- **The mission control panel** sits at the side. You can drag its edge to make it wider or narrower, or hide it.
- **The Mission steps menu** lists four numbered steps, plus **Learn** and **Advanced**:

| # | Step (English / Türkçe) | What it is for |
|---|---|---|
| 1 | Place & time | Choose where and when |
| 2 | Results | See what happens at that spot |
| 3 | Compare & recommend | Find better spots or better dates |
| 4 | Landing & surface | Plan the landing and test whether it works |
| – | Learn | Two short lessons for beginners |
| – | Advanced | Research tools for experts |

Some steps contain several pages. When a step is open, small tabs at the top of the panel let you switch between them.

---

## Step 1 · Place & time

**What it is:** where you set up your mission.

**What you can do:**

- **Pick a landing spot.** Click the Moon in the 3D view. It zooms to the south pole. Click a point inside the dashed ring, because the app only has detailed maps of that area. You can also choose a famous site from the list or type coordinates yourself.
- **Set the observer height.** This is how high above the ground your "eyes" (for example a camera or solar panel) are. Higher eyes can see over small bumps.
- **Choose the start date and how long to watch.** Use the date strip under the 3D view to set the start, then choose a duration in hours or days.
- **Refine transitions (optional).** Tick this to check sunrise and sunset moments more precisely. It is slower.
- **Press "Run analysis".** The app starts calculating. The first time can take a few minutes because it fetches NASA data. You may leave the page; your analysis is remembered on this device.
- **Watch the flight.** Press **Simulate flight** to watch the spacecraft fly from Earth to the Moon in six stages: launch, parking orbit around Earth, coast to the Moon, orbit around the Moon, descent and landed. You can pause, restart, drag the slider, or click a stage to jump to it. The camera buttons let you look from Earth, the Moon, the pole, the spacecraft, the launch or the landing.

> The flight is only an illustration of the order of events. Earth and Moon positions are real, but the spacecraft's path is not a calculated flight.

### Scene details

A small **Scene details** menu above the panel opens three extra views:

- **Flight details**: the date, mission time and distances between Earth, Moon and spacecraft during the flight.
- **Surface operations** (appears only after a successful landing): drive a rover across the real terrain, see the lander's basic data and check current solar storm conditions.
- **Candidates**: the landing spots and routes found by the Compare and Landing steps, shown on the 3D view.

---

## Step 2 · Results

**What it is:** the answer to "what will I see from this spot?"

**What you can do:**

- **Play the window like a video.** Press play or drag the time slider. A short headline tells you the situation at each moment:
  - *Sun and Earth both visible*: panels can charge and Earth is in view.
  - *Sun only*: panels charge, but you cannot talk to Earth directly.
  - *Earth only*: you can talk to Earth, but you are in shadow and on batteries.
  - *Neither*: shadow and no direct contact.
  - *Not enough data*: the app cannot tell at this moment.
- **Read the horizon picture.** The grey skyline is the shape of the mountains around you in every direction. If the Sun or Earth dot is above the skyline, you can see it. If it is below, the terrain blocks it.
- **See the totals.** Total sunlight, total time Earth is in view, and the longest stretch of darkness.
- **Open the extra cards:**
  - **Space weather**: whether solar storms or radiation could affect your window.
  - **Share and export**: download a table (CSV), a picture of the chart, or a printable report; copy a link; or save a scenario file someone else can reload.
  - **Calculation details and limits**: what data and assumptions were used, and what the app does *not* check.
- **Save the result.** Give it a name and keep it in your **Saved results** list in this browser. Tick two saved results to compare them.

> A result describes geometry only. "The Sun is visible" does not guarantee enough power, and "Earth is visible" does not guarantee a working radio link.

---

## Step 3 · Compare & recommend

This step has up to three pages.

### Recommend

**What it is:** a helper that ranks landing spots for you.

**What you can do:** choose 2 to 6 candidate spots. The app scores each one on sunlight, darkness, contact with NASA's ground antennas on Earth (the Deep Space Network) and how steep the ground is. It explains every score and every rejection. You can also ask it to check whether the lander can reach each spot and how much fuel that takes.

### Time search

**What it is:** a helper that finds good *dates* for a spot.

**What you can do:** set your conditions, for example "at least this much sunlight". The app tries up to 16 start times within a month and shows which ones meet them. Click a result to open it.

> It checks a limited set of start times, so it may not find every good date.

### Compare

**What it is:** a side-by-side comparison of two scenarios, called A and B.

**When it appears:** after you choose to compare from your results (for example with **Compare with shown** in Saved results).

**What you can do:**

- Keep A as your baseline and try a different spot or a later date as B. You can also pick B from your saved results.
- Play both together. The 3D view splits in two, and each half shows the sky of its own scenario at the same moment.
- Read live messages such as "A has collected more sunlight so far".
- See a final verdict: which scenario wins on the three key measures (sunlight, darkness and Earth visibility), or whether neither wins on all three.
- Save the comparison and reopen it later.

---

## Step 4 · Landing & surface

This step has two pages.

### Simulation

**What it is:** a test flight of the landing with possible problems added.

**What you can do:** after you finish an analysis in Step 1, pick a problem scenario (or none) and run the simulation. You can also build your own by adding problems with their timing, such as an engine losing power, a fuel leak, a navigation error, a weaker solar panel or a lost radio link.

The mission passes four checks in order:

1. **Descent**: does the lander touch down gently, or does it crash or run out of fuel?
2. **Touchdown ground**: is the ground flat enough, or does the lander tip over?
3. **Battery**: does the battery last through the dark periods?
4. **Contact with Earth**: does Earth come into view soon enough after landing, and do the gaps between contacts stay short enough?

The result is worked out from your spot, date, vehicle and problems; it is not a random roll. If the app lacks data for a check, it says the result is unclear instead of guessing. Press **watch** to see the outcome in the 3D view, including crash or tip-over scenes.

> This is a learning simulation, not a safety certificate.

### Descent

**What it is:** a plan for the lander's trip from orbit to the ground.

**What you can do:** compute the descent for your spot and see each stage (leaving orbit, braking, approach, final descent) with its timing and fuel use, plus a height-versus-distance chart. The default vehicle is the Apollo 11 Lunar Module. You can also compare several routes to save fuel.

Under **More analyses**, open any of these:

- **Risk and uncertainty**: runs hundreds of landings with small random errors and shows how often the landing succeeds and where the lander ends up.
- **Backup landing sites**: finds safe nearby spots the lander could still reach mid-descent if the main plan fails.
- **Digital twin**: compares what the lander's sensors think is happening with what is really happening when something goes wrong.
- **After landing: surface routes**: plans rover drives to interesting targets that avoid steep slopes.
- **Terrain-aware landing recovery**: picks backup targets from the real terrain map and replans the landing after problems, taking signal delay into account.

---

## Learn · Classroom

**What it is:** two short lessons for students and beginners. No setup needed.

**What you can do:** start a lesson about the Malapert Massif, a mountain near the south pole. One lesson shows a 24-hour day; the other shows the same place 48 hours later, so you can see how the light changes. Play, pause or change the speed. A plain sentence explains each moment, for example "Only part of the Sun clears the ridge". You can open the full analysis behind a lesson at any time.

---

## Advanced

These pages are research tools for experts. You can skip them as a beginner.

| Page (English / Türkçe) | In one sentence |
|---|---|
| Campaign | Plans several missions together when they must share landing spots, time and ground antennas. |
| Self-healing | Replays the same problems under the original plan and under replanned versions to see which recovers better. |
| Problem scaling | Measures how solving time grows as scheduling problems get bigger. |
| Relay orbit selection | Chooses relay satellites around the Moon that can pass messages to Earth when Earth is hidden. |
| Shared antenna | Compares each mission choosing alone with one shared schedule for a crewed lander and two robot missions. |
| Solver arena | Replays a regular computer method and a quantum-style method on the same task and compares their results. |

> The quantum experiments are small tests. They do not show that quantum computers beat regular ones here.

---

## Always available at the top of the screen

- **Home**: the opening story page.
- **Mission**: the mission control screen described above.
- **Sources**: where the data comes from (NASA terrain maps and JPL Horizons positions of the Sun and Earth).
- **Assistant** (optional): ask questions about your results in plain words. It may be switched off on some servers.
- **Language**: switch between English and Turkish.

## Good to know

- The detailed map only covers the area near the south pole (80–90° south).
- Durations are estimates based on the terrain map and on samples taken every few minutes.
- The app shows what is geometrically possible. It does **not** certify landing safety, power, temperature or radio links.
