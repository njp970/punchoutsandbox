"""UNSPSC classification data for the mock catalogue.

=============================================================================
PROVENANCE — READ BEFORE TRUSTING A TITLE
=============================================================================
Every code below is a verbatim `Commodity` / `Commodity Name` pair extracted
from a 71,502-row UNSPSC codeset published as OPEN DATA by Oklahoma OMES
(https://data.ok.gov/dataset/unspsc-codes, "No License Provided", last updated
2019-10-31). **No code here was inferred or invented.** Five were spot-checked
against independent sources and all matched.

The file is roughly a v19–v21 vintage. UNSPSC very rarely deletes commodities,
so the CODES should still resolve against the current release — but the TITLES
may have been revised, so this module must not claim to carry current official
titles. `title_is_current` is deliberately absent rather than set to a
comfortable default.

Why UNSPSC and not ECLASS: ECLASS is not an open dataset. ECLASS e.V. asserts
copyright over the dictionary and licenses redistribution — which is precisely
what shipping a codeset inside a public repository would be. Its Terms of Use
§2.4 makes obtaining the data from anywhere other than four named sources
unauthorised outright, and §5.2.1 makes "isolated use of the ECLASS structure"
beyond a host application require a separate licence a downstream cloner will
not have. So this sandbox ships **no ECLASS code data at all**, while still
exercising the multi-domain `Classification` element shape (see
`ECLASS_PLACEHOLDER_DOMAIN` below). If a user wants real ECLASS values, they
supply their own licensed dictionary. ECLASS ToU §4.3.2 directs
development-and-testing users to info@eclass.de — the intended, and probably
free, route if we ever want real codes here.

⚠️ UNSPSC IS NOT PERMISSIVELY LICENSED EITHER, and the position changed
recently enough that most advice about it is stale:

- **Governance moved back to UNDP on 2025-01-01**; GS1 US no longer manages
  it. The official home is https://www.undp.org/unspsc.
- **`unspsc.org` is now a squatted sweepstakes-casino affiliate site.** Do not
  link to it. Note the cXML DTD and User's Guide both still point implementers
  there, so that pointer in the vendored DTDs is dead and hostile.
- UNDP publishes the full codeset free and without registration, but the only
  applicable written terms (https://www.undp.org/copyright-terms-use) grant
  "personal, non-commercial use, **without any right to resell or redistribute
  them or to compile or create derivative works therefrom**."

So neither standard grants redistribution. The reason UNSPSC is nonetheless
what ships here is provenance: this data came from a US state government open
-data publication, not from UNDP's own file, which is the least exposed of the
available routes and is attributable to a publisher who chose to release it.
That is a risk judgement, not a licence. Before this repository goes public,
the cleanest fix is to replace the titles below with our own short
descriptions and keep only the bare 8-digit codes, which are facts.

Note `56101702` genuinely is spelled "accesories" in the official data. It is
kept verbatim — a real catalogue contains real typos, and a harness whose data
is cleaner than reality trains people for a world that does not exist.

=============================================================================
HOW cXML CARRIES THIS
=============================================================================
From the current DTD, verbatim:

    <!ELEMENT Classification (#PCDATA)>
    <!ATTLIST Classification domain %string; #REQUIRED
                             code   %string; #IMPLIED >

and `ItemDetail` requires `Classification+` — **one or more**. Multi-scheme
classification is legal and normal:

    <Classification domain="UNSPSC">44103103</Classification>
    <Classification domain="eCl@ss">...</Classification>
    <Classification domain="MaterialGroup">Z001</Classification>

`domain` is a free string, so the wild is full of variants. We are PERMISSIVE
on input and CANONICAL on output — see `normalise_domain`. That asymmetry is
deliberate and is the behaviour a real supplier has to have.
"""
from __future__ import annotations

import re
from typing import Optional

# The canonical domain string we always emit.
UNSPSC_DOMAIN = "UNSPSC"

# Recognised on input, mapped to UNSPSC_DOMAIN. Confirmed variants plus the
# version-suffixed forms that appear in real supplier traffic.
_DOMAIN_ALIASES = {
    "unspsc", "un spsc", "spsc", "unspsccode", "commoditycode",
    "unspsc/unspsc",  # yes, really — seen in badly-built supplier sites
}

# Domains that are NOT UNSPSC and must not be silently coerced into it.
OTHER_KNOWN_DOMAINS = ("eCl@ss", "ETIM", "MaterialGroup", "NIGP", "CPV", "Custom")

# We exercise the element shape without shipping licensed data. See docstring.
ECLASS_PLACEHOLDER_DOMAIN = "eCl@ss"


def normalise_domain(domain: str) -> str:
    """Map a supplier-supplied `Classification@domain` onto a canonical name.

    Strips version suffixes (`UNSPSC-v13`, `UNSPSC_v13.5`, `unspsc-13`) because
    they are extremely common and carry no information we act on. Returns the
    input unchanged when it is not recognisably UNSPSC — coercing an unknown
    domain into UNSPSC would silently reclassify a supplier's ETIM or
    MaterialGroup codes as commodity codes, which is worse than not
    understanding them."""
    if not domain:
        return domain
    stripped = re.sub(r"[-_\s]*v?\d+(\.\d+)*$", "", domain.strip(), flags=re.I)
    if stripped.lower().replace("-", "").replace("_", "").replace(" ", "") in {
        a.replace(" ", "").replace("/", "") for a in _DOMAIN_ALIASES
    } or stripped.lower() == "unspsc":
        return UNSPSC_DOMAIN
    return domain.strip()


def is_valid_code(code: str) -> bool:
    """Eight digits, no punctuation. UNSPSC is normally written `44.12.16.15`
    or `44-12-16-15` in human-facing documents and MUST be sent unpunctuated —
    every platform researched says so, and a vendor's own published sample
    still got it wrong (`UNSPC 234992835`). Ten-digit codes carry the business
    function suffix, which is almost never used in punchout and which we do
    not emit; they are rejected here rather than truncated, because silently
    dropping two digits changes the meaning."""
    return bool(re.fullmatch(r"\d{8}", code or ""))


def segment_of(code: str) -> str:
    """First two digits — the UNSPSC segment. Trailing zeros denote roll-up
    levels: `43000000` segment, `43210000` family, `43211600` class,
    `43211602` commodity."""
    return code[:2] if is_valid_code(code) else ""


# =========================================================================== #
# The codes. 200 commodity codes across the categories a general business
# supplies / IT / industrial supplier would plausibly carry.
# =========================================================================== #
UNSPSC: dict[str, str] = {
    # --- Paper -----------------------------------------------------------
    "14111507": "Printer or copier paper",
    "14111525": "Multipurpose paper",
    "14111514": "Paper pads or notebooks",
    "14111530": "Self adhesive note paper",
    "14111518": "Index cards",
    "14111527": "Carbonless paper",
    # --- Writing instruments ---------------------------------------------
    "44121704": "Ball point pens",
    "44121701": "Rollerball pens",
    "44121716": "Highlighters",
    "44121708": "Markers",
    "44121706": "Wooden pencils",
    "44121705": "Mechanical pencils",
    "44121804": "Erasers",
    "44121802": "Correction fluid",
    # --- Filing, desk, office machines -----------------------------------
    "44122003": "Binders",
    "44122011": "Folders",
    "44122017": "Hanging folders or accessories",
    "44122035": "Lever arch file",
    "44122104": "Paper clips",
    "44122107": "Staples",
    "44121615": "Staplers",
    "44121618": "Scissors",
    "44111503": "Desktop trays or organizers",
    "44111905": "Dry erase boards or accessories",
    "44101603": "Paper shredding machines or accessories",
    "44101808": "Scientific calculator",
    # --- Print consumables -----------------------------------------------
    "44103103": "Printer or facsimile toner",
    "44103105": "Ink cartridges",
    "44103127": "Photocopier toner",
    "44103109": "Printer or facsimile or photocopier drums",
    "44103112": "Printer ribbon",
    "44103125": "Printer maintenance kit",
    # --- Furniture --------------------------------------------------------
    "56112102": "Task seating",
    "56112104": "Executive seating",
    "56112103": "Guest seating",
    "56112106": "Stool seating",
    "56101703": "Desks",
    "56101702": "Filing cabinets or accesories",  # sic — typo is in the official data
    "56101706": "Conferencing tables",
    "56101507": "Bookcases",
    "56101530": "Storage cabinets",
    "56101520": "Lockers",
    "56101510": "Partitions",
    # --- IT hardware ------------------------------------------------------
    "43211503": "Notebook computers",
    "43211507": "Desktop computers",
    "43211509": "Tablet computers",
    "43211501": "Computer servers",
    "43211902": "Liquid crystal display LCD panels or monitors",
    "43211903": "Touch screen monitors",
    "43212002": "Monitor arms or stands",
    "43201803": "Hard disk drives",
    "43201830": "Solid state drive SSD",
    "43201827": "Portable hard disk storage device",
    "43201835": "Network attached storage NAS device",
    "43201402": "Memory module cards",
    "43202010": "Pen or flash drive",
    "43202005": "Flash memory storage card",
    "43222612": "Network switches",
    "43222609": "Network routers",
    "43222640": "Wireless access point",
    "43223303": "Datacom patch cord",
    "43223309": "Patch panel",
    # --- IT peripherals & imaging ----------------------------------------
    "43211706": "Keyboards",
    "43211708": "Computer mouse or trackballs",
    "43211711": "Scanners",
    "43211701": "Bar code reader equipment",
    "43211602": "Docking stations",
    "43211607": "Computer speakers",
    "43211609": "Universal serial bus hubs or connectors",
    "43211802": "Mouse pads",
    "43211619": "Notebook computer carrying case",
    "43202222": "Computer cable",
    "43191609": "Phone headsets",
    "43212104": "Inkjet printers",
    "43212105": "Laser printers",
    "43212110": "Multi function printers",
    "43212115": "Bar code printer",
    "45111609": "Multimedia projectors",
    "39121011": "Uninterruptible power supply UPS",
    # --- Janitorial & cleaning -------------------------------------------
    "47131805": "General purpose cleaners",
    "47131824": "Glass or window cleaners",
    "47131803": "Household disinfectants",
    "47131801": "Floor cleaners",
    "47131810": "Dishwashing products",
    "47131502": "Cleaning cloths or wipes",
    "47131604": "Brooms",
    "47131618": "Wet mops",
    "47131619": "Mop heads",
    "47121701": "Trash bags",
    "47121702": "Waste containers or rigid liners",
    "47131701": "Paper towel dispensers",
    "47131704": "Institutional soap or lotion dispensers",
    "14111703": "Paper towels",
    "14111704": "Toilet tissue",
    "14111701": "Facial tissues",
    "53131626": "Hand sanitizer",
    "47131905": "Spill kits",
    "47131901": "Absorbent mats",
    # --- PPE ---------------------------------------------------------------
    "46181504": "Protective gloves",
    "46181541": "Chemical resistant gloves",
    "46181536": "Anti cut gloves",
    "46181507": "Safety vests",
    "46181531": "Reflective apparel or accessories",
    "46181503": "Protective coveralls",
    "46181532": "Lab coats",
    "46181604": "Safety boots",
    "46181605": "Safety shoes",
    "46181701": "Hard hats",
    "46181802": "Safety glasses",
    "46181804": "Goggles",
    "46181901": "Ear plugs",
    "46181902": "Ear muffs",
    "46182002": "Respirators",
    "46182001": "Masks or accessories",
    "46181810": "Eyewashers or eye wash stations",
    # --- First aid & site safety ------------------------------------------
    "42172001": "Emergency medical services first aid kits",
    "42311505": "Bandages or dressings for general use",
    "42311511": "Gauze bandages",
    "42311703": "Medical or surgical tapes for skin attachment",
    "42171702": "First aid blankets",
    "46191601": "Fire extinguishers",
    # --- Catering ----------------------------------------------------------
    "52151504": "Domestic disposable cups or glasses or lids",
    "52151503": "Domestic disposable flatware",
    "52151507": "Domestic disposable drinking straws",
    "52151506": "Domestic disposable food containers",
    "52151502": "Domestic disposable dishes",
    "14111705": "Paper napkins or serviettes",
    "50201706": "Coffee",
    "50201713": "Tea bags",
    # --- Packaging & shipping ---------------------------------------------
    "24121503": "Packaging boxes",
    "24141601": "Bubble wrap",
    "24141603": "Cushioning",
    "24141606": "Packing peanuts",
    "24141501": "Stretch wrap films",
    "24141502": "Shrink wrap films",
    "31201517": "Packaging tape",
    "24111503": "Plastic bags",
    "24121502": "Packaging pouches or bags",
    "24141519": "Steel packing band or strapping",
    # --- Electrical --------------------------------------------------------
    "26111702": "Alkaline batteries",
    "26111701": "Rechargeable batteries",
    "26111711": "Lithium batteries",
    "26121536": "Extension cord",
    "39121703": "Cable ties",
    "39101605": "Fluorescent lamps",
    "39101619": "Compact fluorescent CFL lamps",
    "32111503": "Light emitting diodes LEDs",
    "26121606": "Coaxial cable",
    "26121607": "Fiber optic cable",
    "39121601": "Circuit breakers",
    "39121603": "Miniature circuit breakers",
    # --- Hand tools --------------------------------------------------------
    "27111701": "Screwdrivers",
    "27111703": "Socket sets",
    "27111707": "Adjustable wrenches",
    "27111710": "Hex keys",
    "27111503": "Utility knives",
    "27112108": "Needlenose pliers",
    "27112115": "Locking pliers",
    "27111801": "Tape measures",
    "27113201": "General tool kits",
    # --- Power tools -------------------------------------------------------
    "27112703": "Power drills",
    "27112709": "Power saws",
    "27112749": "Angle grinder",
    "27112713": "Impact wrenches",
    "27112708": "Power sanders",
    "27112717": "Heat guns",
    # --- Laboratory --------------------------------------------------------
    "41121607": "Universal pipette tips",
    "41121803": "Laboratory beakers",
    "41121804": "Laboratory flasks",
    "41121812": "Laboratory dishes",
    "41122601": "Microscope slides",
    "41121806": "Laboratory vials",
    "41121701": "Multipurpose or general test tubes",
    "41121703": "Centrifuge tubes",
    # --- Medical / clinical ------------------------------------------------
    "42132203": "Medical exam or non surgical procedure gloves",
    "42132205": "Surgical gloves",
    "42142523": "Hypodermic needle",
    "42142609": "Medical syringe with needle",
    "42311512": "Gauze sponges",
    # --- MRO / fasteners ---------------------------------------------------
    "31161504": "Machine screws",
    "31161506": "Sheet metal screws",
    "31161509": "Drywall screws",
    "31161620": "Hexagonal bolts",
    "31161727": "Hexagonal nuts",
    "31161716": "Locknuts",
    "31161807": "Flat washers",
    "31161801": "Locking washers",
    "31162006": "Wire nails",
    "31162201": "Blind rivets",
    "31191501": "Abrasive papers",
    "15121514": "Spray lubricants",
    "15121902": "Grease",
    "31201501": "Duct tape",
    "31201503": "Masking tape",
    "31201514": "Polytetrafluoroethylene PTFE thread sealing tape",
    # --- Facilities --------------------------------------------------------
    "30191501": "Ladders",
    "30191506": "Platform step ladder",
    "31211513": "Marking paint",
    "46171501": "Padlocks",
    "40141607": "Ball valves",
}


# =========================================================================== #
# Units of measure
# =========================================================================== #
# cXML requires UN/CEFACT Recommendation 20 codes. Reality does not comply,
# and the sandbox has to model reality.
#
# Verified against the official UNECE `rec20_Rev17e-2021.xlsx` (Rev 17, 2021 —
# current; there is no Rev 18) and `rec21_Rev12e_Annex-V-VI_2021.xls`. Three
# findings reshape this table:
#
# 1. **`PCE`, `DZ` and `ROL` are not Rec 20 codes at all**, in Rev 16 or 17.
#    `PCE` is nonetheless everywhere, because SAP ships internal unit `ST`
#    (Stück) with ISO code `PCE` — so SAP's own ISO mapping emits a
#    non-conformant value.
# 2. **Every packaging code was DELETED from Rec 20 and moved to Rec 21**,
#    where it takes an `X` prefix: `BX`→`XBX`, `CT`→`XCT`, `CS`→`XCS`,
#    `PK`→`XPK`, `BG`→`XBG`, `RO`→`XRO`. Emitting the bare two-letter form is
#    the single most common catalogue nonconformity, and nothing rejects it.
# 3. **Every count unit procurement actually uses is INFORMATIVE only.**
#    `EA`, `H87`, `DZN`, `SET`, `PR`, `NAR`, `RM` are all level 3, absent from
#    the normative Annex I. `C62` ("one") is the sole normative count unit.
#    So "use the normative list" is not advice anyone can follow.
CANONICAL_UOM = "EA"

# THE TRAP THAT MUST NOT BE AUTOMATED AWAY.
#
# SAP internal `ST` = Stück = each. **Rec 20 `ST` = SHEET.** An SAP shop
# leaking its internal code instead of the mapped ISO code sends "sheet" to a
# conformant reader that believes it, and no validator anywhere catches it
# because both sides think `ST` is a known code.
#
# So `ST` is NOT in the alias table. Resolving it silently — in either
# direction — would make this sandbox commit the exact class of error it
# exists to detect. It is reported as ambiguous and the user decides.
#
# `PF` is the same shape of trap going the other way: Rec 20 `PF` was
# "pallet (lift)", but Rec 21 `PF` is **"Pen"** (an animal enclosure), so the
# mechanical `X`-prefix migration that is correct for every other packaging
# code silently turns pallets into livestock pens. Also excluded.
AMBIGUOUS_UOM: dict[str, str] = {
    "ST": (
        "Rec 20 'ST' means SHEET. SAP uses 'ST' internally for Stück (each) — "
        "if this came from an SAP system it probably means EACH, and a "
        "conformant reader will take it as SHEET. Map it explicitly; do not "
        "let anything guess."
    ),
    "PF": (
        "Rec 20 'PF' was 'pallet (lift)' and is deleted. Rec 21 'PF' is 'Pen' "
        "(an animal enclosure), so the mechanical PF->XPF migration that is "
        "correct for every other packaging code turns pallets into livestock "
        "pens. The pallet code is Rec 21 'PX', i.e. 'XPX'."
    ),
}

# Input aliases, mapped to the code we would EMIT. Deliberately generous on
# input: a sandbox stricter than the systems its users integrate with would be
# useless. Packaging aliases resolve to the conformant X-prefixed Rec 21 form,
# so the sandbox emits what the spec asks for while accepting what the world
# sends.
UOM_ALIASES: dict[str, str] = {
    # Count. `PCE` and `PC` are not Rec 20 codes but are ubiquitous (see above).
    "ea": "EA", "each": "EA", "ea.": "EA", "e": "EA",
    "pce": "EA", "pc": "EA", "pcs": "EA", "piece": "EA", "pieces": "EA",
    "h87": "H87", "c62": "C62", "nar": "NAR",
    "unit": "EA", "units": "EA", "un": "EA",
    # Packaging — Rec 21, X-prefixed. Bare forms accepted, conformant emitted.
    "bx": "XBX", "box": "XBX", "boxes": "XBX", "xbx": "XBX",
    "cs": "XCS", "case": "XCS", "xcs": "XCS",
    "ct": "XCT", "carton": "XCT", "xct": "XCT",
    "pk": "XPK", "pack": "XPK", "xpk": "XPK",
    "pa": "XPA", "packet": "XPA", "xpa": "XPA",
    "bg": "XBG", "bag": "XBG", "xbg": "XBG",
    "ro": "XRO", "rol": "XRO", "roll": "XRO", "xro": "XRO",
    "rl": "XRL", "reel": "XRL", "xrl": "XRL",
    "tu": "XTU", "tube": "XTU", "xtu": "XTU",
    "dr": "XDR", "drum": "XDR", "xdr": "XDR",
    # Count units that survived in Rec 20.
    "rm": "RM", "ream": "RM",
    "dzn": "DZN", "dz": "DZN", "dozen": "DZN",
    "pr": "PR", "pair": "PR", "npr": "PR",   # NPR is deprecated: "use pair"
    "set": "SET", "kt": "KT", "kit": "KT",
    # Physical units — all normative Annex I.
    "kgm": "KGM", "kg": "KGM",
    "grm": "GRM", "g": "GRM",
    "tne": "TNE", "tonne": "TNE",
    "ltr": "LTR", "l": "LTR", "litre": "LTR", "liter": "LTR",
    "mtr": "MTR", "m": "MTR", "metre": "MTR", "meter": "MTR",
    "cmt": "CMT", "mmt": "MMT", "mtk": "MTK", "mtq": "MTQ",
    "fot": "FOT", "foot": "FOT", "inh": "INH", "inch": "INH",
    "hur": "HUR", "hr": "HUR", "hour": "HUR",
    "day": "DAY",
}

# Rec 21 packaging codes carry an X prefix when used as a unit of measure.
REC21_PACKAGING_PREFIXED = re.compile(r"^X[A-Z0-9]{2}$")

# Deleted from Rec 20 and moved to Rec 21. Sending the bare form is legal-
# looking, extremely common, and nonconformant.
REC20_DELETED_PACKAGING = frozenset(
    {"BX", "CS", "CT", "PK", "PA", "BG", "RO", "RL", "TU", "JR", "TN",
     "CY", "CA", "BE", "ST", "PF"}
)

# Not Rec 20 codes in any revision we could verify, despite being widespread.
NOT_REC20 = frozenset({"PCE", "DZ", "ROL"})


def normalise_uom(value: str) -> tuple[str, Optional[str]]:
    """Return `(emitted_code, advisory_or_None)`.

    The advisory is the point. A supplier sending `BX` gets a working cart AND
    a note that the code was deleted from Rec 20, that the conformant value is
    `XBX`, that JAGGAER would silently coerce an unrecognised unit to `EA` —
    turning a box of 100 into 100 items — and that Coupa would fail the import
    outright instead. Same input, three outcomes, none of them an error."""
    raw = (value or "").strip()
    if not raw:
        return CANONICAL_UOM, "No unit of measure supplied; defaulted to EA."

    upper = raw.upper()
    if upper in AMBIGUOUS_UOM:
        return raw, AMBIGUOUS_UOM[upper]
    if upper in NOT_REC20:
        mapped = UOM_ALIASES.get(raw.lower(), raw)
        return mapped, (
            f"'{raw}' is not a UN/CEFACT Rec 20 code in any verified revision, "
            "though it is very widely sent — SAP ships internal unit ST with "
            f"ISO code PCE. Emitting '{mapped}' instead."
        )
    if upper in REC20_DELETED_PACKAGING:
        mapped = UOM_ALIASES.get(raw.lower(), raw)
        return mapped, (
            f"'{raw}' was deleted from Rec 20 and moved to Rec 21, where it "
            f"takes an X prefix: '{mapped}'. The bare form is what most "
            "catalogues send and nothing rejects it."
        )

    mapped = UOM_ALIASES.get(raw.lower())
    if mapped is None:
        return raw, (
            f"'{raw}' is not a unit of measure this sandbox recognises. "
            "JAGGAER silently maps unrecognised units to EA — so a box of 100 "
            "becomes 100 individual items — while Coupa fails the cart import."
        )
    if mapped == raw:
        return mapped, None
    return mapped, f"Normalised '{raw}' to '{mapped}'."
