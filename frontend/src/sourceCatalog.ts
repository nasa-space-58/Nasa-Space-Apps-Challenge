/**
 * Static provenance catalog shown in the Sources dialog.
 *
 * Every entry mirrors a source recorded elsewhere in the repository (asset
 * manifests in public/nasa and public/models, shared/*.json source fields,
 * shared/constants.py and the docs/ pages). Titles and credits are proper
 * names and stay untranslated; each entry's usage text lives in the i18n
 * dictionaries under app.sources.use.<id>. Site coordinate sources are added
 * at runtime from the backend presets.
 */

export type SourceLink = {
  /** Literal link text, used as-is in every language. */
  label?: string;
  /** i18n key for translated link text. */
  labelKey?: string;
  href: string;
};

export type SourceEntry = {
  id: string;
  title: string;
  /** Primary link; omitted when the source has no stable public URL. */
  href?: string;
  credit: string;
  extra?: SourceLink[];
};

export type SourceGroup = {
  id: string;
  entries: SourceEntry[];
};

const M2M_30044_URL =
  "https://ntrs.nasa.gov/api/citations/20250002845/downloads/M2M-30044%20Lunar%20Surface%20Data%20Book%20Baseline_Final.pdf";
const TN_D_6846_URL =
  "https://ntrs.nasa.gov/api/citations/19720018205/downloads/19720018205.pdf";
const APOLLO_11_REPORT_URL =
  "https://www.nasa.gov/wp-content/uploads/static/apollo50th/pdf/A11_MissionReport.pdf";

export const SOURCE_GROUPS: SourceGroup[] = [
  {
    id: "models",
    entries: [
      {
        id: "lro",
        title: "Lunar Reconnaissance Orbiter (A)",
        href: "https://science.nasa.gov/3d-resources/lunar-reconnaissance-orbiter-a/",
        credit: "NASA / Christopher R. Meaney",
      },
      {
        id: "apollo",
        title: "Apollo Lunar Module",
        href: "https://science.nasa.gov/3d-resources/apollo-lunar-module/",
        credit: "NASA / Michael D. Carbajal",
      },
      {
        id: "mobileLauncher",
        title: "Mobile Launcher (assembled)",
        href: "https://science.nasa.gov/3d-resources/mobile-launcher/",
        credit: "NASA / Samuel H. Neblett",
      },
      {
        id: "rocket",
        title: "Rocket Launch Site – Rocket in Stages",
        href: "https://1designtool.com/3d/models/rocket-launch-site-rocket-in-stages",
        credit: "StoicSoft / 1DesignTool",
        extra: [
          { labelKey: "app.sources.link.license", href: "https://1designtool.com/3d/license" },
        ],
      },
      {
        id: "rover",
        title: "Lunar Rover",
        href: "https://sketchfab.com/3d-models/lunar-rover-82cab6e9ada74d12a98bd6a342e83048",
        credit: "Versal (Sketchfab)",
        extra: [{ label: "CC BY 4.0", href: "https://creativecommons.org/licenses/by/4.0/" }],
      },
      {
        id: "moonKit",
        title: "CGI Moon Kit",
        href: "https://svs.gsfc.nasa.gov/4720/",
        credit: "NASA / Goddard / Scientific Visualization Studio / Ernie Wright",
      },
      {
        id: "blueMarble",
        title: "Blue Marble Next Generation",
        href: "https://science.nasa.gov/earth/earth-observatory/blue-marble-next-generation/",
        credit: "NASA Earth Observatory / Reto Stöckli",
      },
    ],
  },
  {
    id: "terrain",
    entries: [
      {
        id: "lola",
        title: "LOLA South Pole DEM (PGDA product 90, LDEM_80S_80MPP_ADJ)",
        href: "https://pgda.gsfc.nasa.gov/products/90",
        credit: "NASA GSFC PGDA · Barker et al. (2023), PSJ 4, 183",
        extra: [
          { label: "DOI 10.60903/gsfcpgda-lola-spole", href: "https://doi.org/10.60903/gsfcpgda-lola-spole" },
          { label: "Barker et al. (2023)", href: "https://doi.org/10.3847/PSJ/acf3e1" },
        ],
      },
      {
        id: "lsdb",
        title: "Moon to Mars (M2M) Lunar Surface Data Book (M2M-30044)",
        href: M2M_30044_URL,
        credit: "NASA Moon to Mars Program",
      },
    ],
  },
  {
    id: "ephemeris",
    entries: [
      {
        id: "horizons",
        title: "JPL Horizons",
        href: "https://ssd.jpl.nasa.gov/horizons/",
        credit: "NASA / JPL Solar System Dynamics",
        extra: [{ labelKey: "app.sources.link.manual", href: "https://ssd.jpl.nasa.gov/horizons/manual.html" }],
      },
      {
        id: "astroPar",
        title: "Astrodynamic Parameters (DE440/DE441)",
        href: "https://ssd.jpl.nasa.gov/astro_par.html",
        credit: "NASA / JPL Solar System Dynamics",
      },
      {
        id: "iers",
        title: "IERS Conventions (2010), Technical Note 36",
        href: "https://iers-conventions.obspm.fr/",
        credit: "International Earth Rotation and Reference Systems Service",
      },
      {
        id: "periodicOrbits",
        title: "Three-Body Periodic Orbits API",
        href: "https://ssd-api.jpl.nasa.gov/doc/periodic_orbits.html",
        credit: "NASA / JPL Solar System Dynamics",
        extra: [{ labelKey: "app.sources.link.model", href: "https://ssd.jpl.nasa.gov/tools/periodic_orbits.html" }],
      },
    ],
  },
  {
    id: "mission",
    entries: [
      {
        id: "apollo11Report",
        title: "Apollo 11 Mission Report (MSC-00171)",
        href: APOLLO_11_REPORT_URL,
        credit: "NASA Manned Spacecraft Center",
      },
      {
        id: "tnD6846",
        title: "Apollo Experience Report: Mission Planning for Lunar Module Descent and Ascent (NASA TN D-6846)",
        href: TN_D_6846_URL,
        credit: "NASA · F. V. Bennett (1972)",
      },
      {
        id: "dpsEvaluation",
        title: "Apollo 9 Descent Propulsion System flight evaluation",
        credit: "NASA",
      },
    ],
  },
  {
    id: "communication",
    entries: [
      {
        id: "dsn",
        title: "DSN Telecommunications Link Design Handbook 810-005, module 301, Rev. P",
        href: "https://deepspace.jpl.nasa.gov/dsndocs/810-005/301/301P.pdf",
        credit: "NASA / JPL Deep Space Network",
      },
      {
        id: "swpc",
        title: "NOAA SWPC JSON data services",
        href: "https://services.swpc.noaa.gov/json/",
        credit: "NOAA Space Weather Prediction Center",
        extra: [
          { labelKey: "app.sources.link.scales", href: "https://www.swpc.noaa.gov/noaa-scales-explanation" },
        ],
      },
    ],
  },
  {
    id: "quantum",
    entries: [
      {
        id: "qaoa",
        title: "A Quantum Approximate Optimization Algorithm",
        href: "https://arxiv.org/abs/1411.4028",
        credit: "Farhi, Goldstone, Gutmann (2014)",
      },
      {
        id: "openQuantum",
        title: "Open Quantum / Rigetti Cepheus-1-108Q",
        credit: "Open Quantum · Rigetti Computing",
      },
    ],
  },
];
