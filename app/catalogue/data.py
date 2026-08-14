"""Meridian Supply Co. — the catalogue.

*Every company and brand named here is INVENTED. See `models.py` for why that
is a hard constraint rather than a stylistic choice.*

Structure: a three-level merchandising tree for browsing, and a flat UNSPSC
code on each product for spend analytics. The two are deliberately decoupled —
see the `models.py` docstring. `CATEGORY_UNSPSC_SPREAD` at the foot of this
file asserts the decoupling actually holds, because it is the kind of property
that quietly degrades into a 1:1 mapping as products get added.

Roughly one product in eight carries a `Quirk`. That ratio is chosen, not
accidental: enough that any realistic browse session meets one, few enough
that the catalogue still looks like a catalogue rather than a test fixture.
"""
from __future__ import annotations

from decimal import Decimal as D

from .models import Category, PriceBreak as PB, Product, Quirk as Q

SUPPLIER_NAME = "Meridian Supply Co."
SUPPLIER_DUNS = "000000000"          # deliberately not a real DUNS
SUPPLIER_DOMAIN = "meridiansupply.example"

# --------------------------------------------------------------------------- #
# Merchandising tree — 3 levels
# --------------------------------------------------------------------------- #
CATEGORIES: tuple[Category, ...] = (
    # 1. Office Supplies
    Category("office", "Office Supplies"),
    Category("office.paper", "Paper & Pads", "office"),
    Category("office.paper.copier", "Copier & Printer Paper", "office.paper"),
    Category("office.paper.pads", "Notebooks & Pads", "office.paper"),
    Category("office.write", "Writing & Correction", "office"),
    Category("office.write.pens", "Pens", "office.write"),
    Category("office.write.pencils", "Pencils & Markers", "office.write"),
    Category("office.file", "Filing & Archiving", "office"),
    Category("office.file.binders", "Binders & Lever Arch", "office.file"),
    Category("office.file.folders", "Folders & Suspension Files", "office.file"),
    Category("office.desk", "Desktop Essentials", "office"),
    Category("office.desk.fixing", "Staplers & Clips", "office.desk"),
    Category("office.desk.organise", "Desk Organisers", "office.desk"),

    # 2. Print & Imaging
    Category("print", "Print & Imaging"),
    Category("print.consumables", "Toner & Ink", "print"),
    Category("print.consumables.laser", "Laser Toner", "print.consumables"),
    Category("print.consumables.inkjet", "Inkjet Cartridges", "print.consumables"),
    Category("print.devices", "Printers & Devices", "print"),
    Category("print.devices.laser", "Laser Printers", "print.devices"),
    Category("print.devices.mfd", "Multifunction Devices", "print.devices"),
    Category("print.doc", "Document Handling", "print"),
    Category("print.doc.shred", "Shredders", "print.doc"),

    # 3. IT & Technology
    Category("it", "IT & Technology"),
    Category("it.compute", "Computing", "it"),
    Category("it.compute.laptops", "Laptops & Notebooks", "it.compute"),
    Category("it.compute.desktops", "Desktops", "it.compute"),
    Category("it.display", "Displays & Docking", "it"),
    Category("it.display.monitors", "Monitors", "it.display"),
    Category("it.display.docks", "Docking Stations", "it.display"),
    Category("it.input", "Input & Peripherals", "it"),
    Category("it.input.keyboards", "Keyboards & Mice", "it.input"),
    Category("it.input.audio", "Headsets & Audio", "it.input"),
    Category("it.storage", "Storage & Memory", "it"),
    Category("it.storage.drives", "Drives & SSDs", "it.storage"),
    Category("it.storage.flash", "Flash Media", "it.storage"),
    Category("it.network", "Networking & Cabling", "it"),
    Category("it.network.active", "Switches & Access Points", "it.network"),
    Category("it.network.cable", "Cables & Patch Leads", "it.network"),
    Category("it.power", "Power Protection", "it"),
    Category("it.power.ups", "UPS Systems", "it.power"),

    # 4. Furniture & Workspace
    Category("furn", "Furniture & Workspace"),
    Category("furn.seating", "Seating", "furn"),
    Category("furn.seating.task", "Task Chairs", "furn.seating"),
    Category("furn.seating.meeting", "Meeting & Visitor Chairs", "furn.seating"),
    Category("furn.desks", "Desks & Tables", "furn"),
    Category("furn.desks.office", "Office Desks", "furn.desks"),
    Category("furn.storage", "Storage & Screens", "furn"),
    Category("furn.storage.cabinets", "Filing Cabinets", "furn.storage"),

    # 5. Facilities & Janitorial
    Category("fac", "Facilities & Janitorial"),
    Category("fac.chem", "Cleaning Chemicals", "fac"),
    Category("fac.chem.general", "Multi-Surface & General", "fac.chem"),
    Category("fac.chem.washroom", "Washroom & Sanitiser", "fac.chem"),
    Category("fac.equip", "Cleaning Equipment", "fac"),
    Category("fac.equip.mops", "Mops & Brooms", "fac.equip"),
    Category("fac.equip.cloths", "Cloths & Wipes", "fac.equip"),
    Category("fac.washroom", "Washroom & Hygiene", "fac"),
    Category("fac.washroom.paper", "Tissue & Hand Towels", "fac.washroom"),
    Category("fac.waste", "Waste Management", "fac"),
    Category("fac.waste.sacks", "Refuse Sacks & Bins", "fac.waste"),
    Category("fac.site", "Site Maintenance", "fac"),
    Category("fac.site.access", "Ladders & Access", "fac.site"),

    # 6. Safety & PPE
    Category("ppe", "Safety & PPE"),
    Category("ppe.hand", "Hand Protection", "ppe"),
    Category("ppe.hand.general", "General Handling Gloves", "ppe.hand"),
    Category("ppe.hand.chem", "Chemical & Cut Resistant", "ppe.hand"),
    Category("ppe.foot", "Foot Protection", "ppe"),
    Category("ppe.foot.boots", "Safety Boots", "ppe.foot"),
    Category("ppe.head", "Head, Eye & Hearing", "ppe"),
    Category("ppe.head.eye", "Safety Eyewear", "ppe.head"),
    Category("ppe.head.hard", "Hard Hats", "ppe.head"),
    Category("ppe.head.ear", "Hearing Protection", "ppe.head"),
    Category("ppe.resp", "Respiratory", "ppe"),
    Category("ppe.resp.masks", "Masks & Respirators", "ppe.resp"),
    Category("ppe.wear", "Workwear & Hi-Vis", "ppe"),
    Category("ppe.wear.hivis", "Hi-Vis Clothing", "ppe.wear"),
    Category("ppe.first", "First Aid", "ppe"),
    Category("ppe.first.kits", "First Aid Kits", "ppe.first"),

    # 7. Catering & Breakroom
    Category("cater", "Catering & Breakroom"),
    Category("cater.disp", "Disposables", "cater"),
    Category("cater.disp.cups", "Cups & Lids", "cater.disp"),
    Category("cater.bev", "Beverages", "cater"),
    Category("cater.bev.hot", "Coffee & Tea", "cater.bev"),

    # 8. Packaging & Warehouse
    Category("pack", "Packaging & Warehouse"),
    Category("pack.boxes", "Boxes & Bags", "pack"),
    Category("pack.boxes.corrugated", "Corrugated Boxes", "pack.boxes"),
    Category("pack.protect", "Protection & Void Fill", "pack"),
    Category("pack.protect.bubble", "Bubble & Cushioning", "pack.protect"),
    Category("pack.wrap", "Wrapping & Tapes", "pack"),
    Category("pack.wrap.tape", "Packaging Tape", "pack.wrap"),

    # 9. Industrial & MRO
    Category("mro", "Industrial & MRO"),
    Category("mro.fast", "Fasteners", "mro"),
    Category("mro.fast.screws", "Screws", "mro.fast"),
    Category("mro.fast.bolts", "Bolts, Nuts & Washers", "mro.fast"),
    Category("mro.hand", "Hand Tools", "mro"),
    Category("mro.hand.drivers", "Screwdrivers & Hex Keys", "mro.hand"),
    Category("mro.hand.measure", "Measuring & Layout", "mro.hand"),
    Category("mro.power", "Power Tools", "mro"),
    Category("mro.power.drills", "Drills & Drivers", "mro.power"),
    Category("mro.elec", "Electrical", "mro"),
    Category("mro.elec.batteries", "Batteries", "mro.elec"),
    Category("mro.elec.lamps", "Lamps & Tubes", "mro.elec"),

    # 10. Laboratory & Clinical
    Category("lab", "Laboratory & Clinical"),
    Category("lab.consum", "Lab Consumables", "lab"),
    Category("lab.consum.pipette", "Pipette Tips", "lab.consum"),
    Category("lab.consum.glass", "Glassware & Tubes", "lab.consum"),
    Category("lab.clinical", "Clinical Consumables", "lab"),
    Category("lab.clinical.gloves", "Examination Gloves", "lab.clinical"),
)

# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #
PRODUCTS: tuple[Product, ...] = (
    # --- Office: paper ------------------------------------------------------
    Product("MSC-1001", "Meridian A4 Copier Paper 80gsm",
            "Meridian A4 white copier paper, 80gsm, 500 sheets per ream. FSC certified, "
            "suitable for all laser and inkjet devices.",
            "office.paper.copier", "14111507", "RM", D("4.85"), "Meridian", "MER-A4-80",
            3, pack_size=1, price_breaks=(PB(5, D("4.45")), PB(20, D("3.99")), PB(100, D("3.60"))),
            aux_token="CTR-STDPAPER"),
    Product("MSC-1002", "Meridian A4 Copier Paper 80gsm, Box of 5",
            "Meridian A4 white copier paper, 80gsm. Box of 5 reams, 2500 sheets total.",
            "office.paper.copier", "14111507", "BX", D("22.50"), "Meridian", "MER-A4-80-BX",
            3, pack_size=5, min_order_qty=1, price_breaks=(PB(10, D("20.95")),),
            aux_token="CTR-STDPAPER"),
    Product("MSC-1003", "Meridian A3 Copier Paper 80gsm",
            "Meridian A3 white copier paper, 80gsm, 500 sheets per ream.",
            "office.paper.copier", "14111525", "RM", D("9.70"), "Meridian", "MER-A3-80", 3),
    Product("MSC-1010", "Quillon Wirebound Notebook A4",
            "Quillon A4 wirebound notebook, 160 pages, 70gsm ruled feint with margin. "
            "Perforated pages and card cover.",
            "office.paper.pads", "14111514", "EA", D("2.35"), "Quillon", "QN-WB-A4",
            5, price_breaks=(PB(10, D("2.10")), PB(50, D("1.85")))),
    Product("MSC-1011", "Quillon Refill Pad A4, Pack of 10",
            "Quillon A4 refill pad, 160 pages ruled feint. Pack of 10 pads.",
            "office.paper.pads", "14111514", "PK", D("14.99"), "Quillon", "QN-RP-A4-10",
            5, pack_size=10),

    # --- Office: writing ----------------------------------------------------
    Product("MSC-1100", "Marlowe Ballpoint Pen Blue, Box of 50",
            "Marlowe retractable ballpoint pen, 1.0mm medium tip, blue ink. Box of 50.",
            "office.write.pens", "44121704", "BX", D("11.40"), "Marlowe", "MW-BP-BL-50",
            2, pack_size=50, price_breaks=(PB(5, D("10.25")), PB(20, D("9.10")))),
    Product("MSC-1101", "Marlowe Rollerball Pen Black 0.7mm",
            "Marlowe liquid ink rollerball, 0.7mm fine tip, black. Smooth-flow cartridge.",
            "office.write.pens", "44121701", "EA", D("1.65"), "Marlowe", "MW-RB-BK-07", 2,
            price_breaks=(PB(12, D("1.45")), PB(60, D("1.28")))),
    Product("MSC-1102", "Marlowe Highlighter Assorted, Wallet of 4",
            "Marlowe chisel-tip highlighter, wallet of 4 assorted fluorescent colours.",
            "office.write.pencils", "44121716", "PK", D("3.20"), "Marlowe", "MW-HL-AS-4",
            2, pack_size=4),
    Product("MSC-1103", "Marlowe HB Pencil, Box of 12",
            "Marlowe HB graphite pencil, cedar barrel, rubber tipped. Box of 12.",
            "office.write.pencils", "44121706", "BX", D("2.75"), "Marlowe", "MW-HB-12",
            2, pack_size=12),

    # --- Office: filing -----------------------------------------------------
    Product("MSC-1200", "Stanmore Lever Arch File A4 Black",
            "Stanmore A4 lever arch file, 70mm capacity, board with polypropylene finish. "
            "Metal shoe and thumb hole.",
            "office.file.binders", "44122035", "EA", D("3.10"), "Stanmore", "ST-LA-A4-BK",
            4, price_breaks=(PB(10, D("2.80")), PB(50, D("2.45")))),
    Product("MSC-1201", "Stanmore Ring Binder A4 2-Ring 25mm",
            "Stanmore A4 presentation ring binder, 2-ring 25mm, white PVC with spine pocket.",
            "office.file.binders", "44122003", "EA", D("1.95"), "Stanmore", "ST-RB-A4-25", 4),
    Product("MSC-1202", "Stanmore Suspension File Foolscap, Box of 50",
            "Stanmore foolscap suspension file, V-base, green manilla with printed tabs "
            "and inserts. Box of 50.",
            "office.file.folders", "44122017", "BX", D("28.90"), "Stanmore", "ST-SF-FC-50",
            4, pack_size=50, price_breaks=(PB(4, D("26.50")),)),
    Product("MSC-1203", "Stanmore Manilla Folder A4, Pack of 100",
            "Stanmore A4 manilla folder, 250gsm, buff. Pack of 100.",
            "office.file.folders", "44122011", "PK", D("12.40"), "Stanmore", "ST-MF-A4-100",
            4, pack_size=100),

    # --- Office: desk -------------------------------------------------------
    Product("MSC-1300", "Ashcroft Heavy Duty Stapler",
            "Ashcroft full-strip heavy duty stapler, 60-sheet capacity, all-metal body "
            "with adjustable throat depth.",
            "office.desk.fixing", "44121615", "EA", D("18.75"), "Ashcroft", "AC-HD-60", 5),
    Product("MSC-1301", "Ashcroft Staples 26/6, Box of 5000",
            "Ashcroft 26/6 galvanised staples. Box of 5000.",
            "office.desk.fixing", "44122107", "BX", D("1.85"), "Ashcroft", "AC-ST-266",
            2, pack_size=5000),
    Product("MSC-1302", "Ashcroft Paper Clips 33mm, Tub of 1000",
            "Ashcroft 33mm nickel-plated paper clips. Tub of 1000.",
            "office.desk.fixing", "44122104", "EA", D("3.45"), "Ashcroft", "AC-PC-33",
            2, pack_size=1000),
    Product("MSC-1303", "Ashcroft 3-Tier Letter Tray Mesh",
            "Ashcroft three-tier stacking letter tray, black wire mesh, A4.",
            "office.desk.organise", "44111503", "EA", D("14.20"), "Ashcroft", "AC-LT-3", 6),

    # --- Print --------------------------------------------------------------
    Product("MSC-2000", "Kestrel Toner Cartridge KX-540 Black",
            "Kestrel KX-540 black laser toner cartridge. Approximate yield 6,000 pages "
            "at 5% coverage. For Kestrel LP-540 series.",
            "print.consumables.laser", "44103103", "EA", D("74.50"), "Kestrel", "KX-540-BK",
            2, price_breaks=(PB(3, D("69.90")), PB(10, D("64.50"))), aux_token="CTR-KESTREL-24"),
    Product("MSC-2001", "Kestrel Toner Cartridge KX-540 Cyan",
            "Kestrel KX-540 cyan laser toner cartridge. Approximate yield 4,000 pages.",
            "print.consumables.laser", "44103103", "EA", D("88.00"), "Kestrel", "KX-540-C",
            2, aux_token="CTR-KESTREL-24"),
    Product("MSC-2002", "Kestrel Drum Unit KD-540",
            "Kestrel KD-540 imaging drum unit. Approximate yield 20,000 pages.",
            "print.consumables.laser", "44103109", "EA", D("112.00"), "Kestrel", "KD-540",
            7, aux_token="CTR-KESTREL-24"),
    Product("MSC-2010", "Kestrel Ink Cartridge JX-22 Black",
            "Kestrel JX-22 black inkjet cartridge, standard yield 400 pages.",
            "print.consumables.inkjet", "44103105", "EA", D("18.40"), "Kestrel", "JX-22-BK", 2),
    Product("MSC-2020", "Kestrel LP-540dn Mono Laser Printer",
            "Kestrel LP-540dn A4 mono laser printer, 40ppm, automatic duplex, gigabit "
            "ethernet and 550-sheet input tray.",
            "print.devices.laser", "43212105", "EA", D("289.00"), "Kestrel", "LP-540DN", 10),
    Product("MSC-2021", "Kestrel MX-720 Colour Multifunction Device",
            "Kestrel MX-720 A3 colour multifunction device. Print, copy, scan and fax, "
            "35ppm colour, 100-sheet duplex document feeder.",
            "print.devices.mfd", "43212110", "EA", D("1845.00"), "Kestrel", "MX-720", 21),
    Product("MSC-2030", "Ashcroft Cross-Cut Shredder P-4 12 Sheet",
            "Ashcroft P-4 security cross-cut shredder, 12-sheet capacity, 23-litre bin, "
            "shreds staples and credit cards.",
            "print.doc.shred", "44101603", "EA", D("165.00"), "Ashcroft", "AC-SH-12", 7),

    # --- IT -----------------------------------------------------------------
    Product("MSC-3000", "Vantor ProBook 14 i5 16GB 512GB",
            "Vantor ProBook 14-inch business notebook. Core i5, 16GB RAM, 512GB NVMe SSD, "
            "Windows 11 Pro, 3-year on-site warranty.",
            "it.compute.laptops", "43211503", "EA", D("1049.00"), "Vantor", "VT-PB14-I5",
            14, price_breaks=(PB(5, D("998.00")), PB(25, D("945.00"))),
            aux_token="CFG-PB14-I5-16-512"),
    Product("MSC-3001", "Vantor ProBook 14 i7 32GB 1TB",
            "Vantor ProBook 14-inch business notebook. Core i7, 32GB RAM, 1TB NVMe SSD, "
            "Windows 11 Pro, 3-year on-site warranty.",
            "it.compute.laptops", "43211503", "EA", D("1489.00"), "Vantor", "VT-PB14-I7",
            14, aux_token="CFG-PB14-I7-32-1024"),
    Product("MSC-3002", "Vantor DeskPro Mini i5 16GB",
            "Vantor DeskPro Mini small form factor desktop. Core i5, 16GB RAM, 512GB SSD.",
            "it.compute.desktops", "43211507", "EA", D("689.00"), "Vantor", "VT-DPM-I5",
            14, aux_token="CFG-DPM-I5-16-512"),
    Product("MSC-3010", "Lumen 27in QHD IPS Monitor",
            "Lumen 27-inch QHD 2560x1440 IPS monitor. USB-C 65W power delivery, height "
            "adjustable stand, HDMI and DisplayPort.",
            "it.display.monitors", "43211902", "EA", D("245.00"), "Lumen", "LM-27Q",
            7, price_breaks=(PB(4, D("232.00")), PB(20, D("218.50")))),
    Product("MSC-3011", "Lumen 24in FHD IPS Monitor",
            "Lumen 24-inch Full HD 1920x1080 IPS monitor with tilt stand.",
            "it.display.monitors", "43211902", "EA", D("129.00"), "Lumen", "LM-24F", 7),
    Product("MSC-3012", "Vantor USB-C Docking Station Dual 4K",
            "Vantor USB-C docking station. Dual 4K DisplayPort, gigabit ethernet, "
            "4x USB-A, 100W power delivery.",
            "it.display.docks", "43211602", "EA", D("179.00"), "Vantor", "VT-DK-4K2", 7),
    Product("MSC-3020", "Vantor Wireless Keyboard & Mouse Set",
            "Vantor wireless keyboard and optical mouse set, 2.4GHz unifying receiver, "
            "UK layout.",
            "it.input.keyboards", "43211706", "EA", D("34.50"), "Vantor", "VT-KM-WL", 5),
    Product("MSC-3021", "Beckwith UC Headset Stereo USB",
            "Beckwith stereo USB headset with noise-cancelling boom microphone. "
            "Certified for major unified communications platforms.",
            "it.input.audio", "43191609", "EA", D("62.00"), "Beckwith", "BW-UC-ST",
            5, price_breaks=(PB(10, D("56.50")),)),
    Product("MSC-3030", "Orrell 1TB NVMe SSD M.2",
            "Orrell 1TB NVMe M.2 2280 internal SSD, PCIe Gen4, up to 5,000MB/s read.",
            "it.storage.drives", "43201830", "EA", D("78.00"), "Orrell", "OR-NV-1T", 4),
    Product("MSC-3031", "Orrell 2TB Portable SSD USB-C",
            "Orrell 2TB portable external SSD, USB-C 3.2 Gen2, hardware encryption.",
            "it.storage.drives", "43201827", "EA", D("158.00"), "Orrell", "OR-PS-2T", 4),
    Product("MSC-3032", "Orrell 64GB USB 3.2 Flash Drive, Pack of 10",
            "Orrell 64GB USB 3.2 flash drive. Pack of 10.",
            "it.storage.flash", "43202010", "PK", D("64.00"), "Orrell", "OR-FD-64-10",
            4, pack_size=10),
    Product("MSC-3040", "Pellworth 24-Port Gigabit Switch",
            "Pellworth 24-port gigabit unmanaged rackmount switch, 1U, fanless.",
            "it.network.active", "43222612", "EA", D("139.00"), "Pellworth", "PW-SW-24G", 7),
    Product("MSC-3041", "Pellworth Wi-Fi 6 Access Point",
            "Pellworth Wi-Fi 6 dual-band ceiling access point, PoE+ powered.",
            "it.network.active", "43222640", "EA", D("168.00"), "Pellworth", "PW-AP-6", 7),
    Product("MSC-3042", "Pellworth Cat6 Patch Lead 2m Grey, Pack of 10",
            "Pellworth Cat6 UTP patch lead, 2m, grey, snagless boot. Pack of 10.",
            "it.network.cable", "43223303", "PK", D("19.50"), "Pellworth", "PW-C6-2-10",
            3, pack_size=10, price_breaks=(PB(10, D("17.80")),)),
    Product("MSC-3050", "Ferrum 1500VA Line Interactive UPS",
            "Ferrum 1500VA/900W line interactive UPS, tower, 8 IEC outlets, LCD display "
            "and USB monitoring.",
            "it.power.ups", "39121011", "EA", D("259.00"), "Ferrum", "FR-UPS-1500", 10),

    # --- Furniture ----------------------------------------------------------
    Product("MSC-4000", "Cartwright Ergo Task Chair Black",
            "Cartwright ergonomic task chair with synchronous mechanism, adjustable "
            "lumbar support, height-adjustable arms and 5-star nylon base.",
            "furn.seating.task", "56112102", "EA", D("189.00"), "Cartwright", "CW-TC-ERG",
            15, price_breaks=(PB(5, D("175.00")), PB(20, D("162.00"))),
            aux_token="CFG-TC-BLK-ARMS"),
    Product("MSC-4001", "Cartwright Mesh Back Task Chair",
            "Cartwright mesh back task chair, breathable backrest, fixed arms.",
            "furn.seating.task", "56112102", "EA", D("124.00"), "Cartwright", "CW-TC-MSH", 15),
    Product("MSC-4002", "Cartwright Cantilever Meeting Chair, Pack of 4",
            "Cartwright cantilever meeting chair, chrome frame, fabric seat and back. "
            "Pack of 4, stackable.",
            "furn.seating.meeting", "56112103", "PK", D("312.00"), "Cartwright", "CW-MC-CN-4",
            20, pack_size=4),
    Product("MSC-4010", "Cartwright Rectangular Desk 1600x800 Oak",
            "Cartwright rectangular office desk, 1600x800mm, oak finish, cable port and "
            "adjustable feet.",
            "furn.desks.office", "56101703", "EA", D("235.00"), "Cartwright", "CW-DK-1680-OK",
            20, aux_token="CFG-DK-OAK-1600"),
    Product("MSC-4020", "Cartwright 3-Drawer Filing Cabinet Steel",
            "Cartwright 3-drawer foolscap filing cabinet, steel, anti-tilt mechanism "
            "and central locking.",
            "furn.storage.cabinets", "56101702", "EA", D("198.00"), "Cartwright", "CW-FC-3D", 20),

    # --- Facilities ---------------------------------------------------------
    Product("MSC-5000", "Brightwell Multi-Surface Cleaner 5L",
            "Brightwell concentrated multi-surface cleaner, 5 litre. Dilutes 1:100.",
            "fac.chem.general", "47131805", "EA", D("12.40"), "Brightwell", "BW-MS-5L",
            3, price_breaks=(PB(4, D("11.20")),)),
    Product("MSC-5001", "Brightwell Glass Cleaner 750ml, Case of 6",
            "Brightwell trigger-spray glass and window cleaner, 750ml. Case of 6.",
            "fac.chem.general", "47131824", "CS", D("14.70"), "Brightwell", "BW-GC-750-6",
            3, pack_size=6),
    Product("MSC-5002", "Brightwell Alcohol Hand Sanitiser 500ml Pump",
            "Brightwell 70% alcohol hand sanitiser gel, 500ml pump bottle.",
            "fac.chem.washroom", "53131626", "EA", D("4.10"), "Brightwell", "BW-HS-500",
            3, price_breaks=(PB(12, D("3.65")), PB(48, D("3.20"))), hazardous=True),
    Product("MSC-5010", "Brightwell Kentucky Mop Head 16oz",
            "Brightwell 16oz Kentucky mop head, colour-coded blue, socket fitting.",
            "fac.equip.mops", "47131619", "EA", D("3.85"), "Brightwell", "BW-KM-16", 4),
    Product("MSC-5011", "Brightwell Microfibre Cloth, Pack of 10",
            "Brightwell microfibre cleaning cloth, 40x40cm, colour-coded. Pack of 10.",
            "fac.equip.cloths", "47131502", "PK", D("6.90"), "Brightwell", "BW-MF-10",
            4, pack_size=10),
    Product("MSC-5020", "Brightwell Centrefeed Roll Blue 2-Ply, Case of 6",
            "Brightwell blue centrefeed roll, 2-ply, 150m. Case of 6 rolls.",
            "fac.washroom.paper", "14111703", "CS", D("18.60"), "Brightwell", "BW-CF-B-6",
            4, pack_size=6, price_breaks=(PB(10, D("16.90")),)),
    Product("MSC-5021", "Brightwell Toilet Roll 2-Ply, Case of 36",
            "Brightwell 2-ply toilet roll, 320 sheets. Case of 36 rolls.",
            "fac.washroom.paper", "14111704", "CS", D("22.40"), "Brightwell", "BW-TR-36",
            4, pack_size=36),
    Product("MSC-5030", "Brightwell Refuse Sack Heavy Duty, Box of 200",
            "Brightwell heavy duty black refuse sack, 18x29x39in, 200 gauge. Box of 200.",
            "fac.waste.sacks", "47121701", "BX", D("24.80"), "Brightwell", "BW-RS-HD-200",
            4, pack_size=200),
    Product("MSC-5040", "Halyard Aluminium Step Ladder 5 Tread",
            "Halyard 5-tread aluminium step ladder, EN131 rated, non-slip treads and "
            "safety handrail.",
            "fac.site.access", "30191506", "EA", D("78.50"), "Halyard", "HY-SL-5", 7),

    # --- PPE ----------------------------------------------------------------
    Product("MSC-6000", "Halyard Nitrile Grip Glove, Pack of 12 Pairs",
            "Halyard nitrile-coated seamless knit glove, EN388 4121X, size 9. "
            "Pack of 12 pairs.",
            "ppe.hand.general", "46181504", "PK", D("14.40"), "Halyard", "HY-NG-9-12",
            3, pack_size=12, price_breaks=(PB(10, D("13.10")), PB(50, D("11.80")))),
    Product("MSC-6001", "Halyard Cut Resistant Glove Level D, Pair",
            "Halyard HPPE cut resistant glove, EN388 4X42D, PU palm coating, size 9.",
            "ppe.hand.chem", "46181536", "PR", D("6.85"), "Halyard", "HY-CR-D-9", 3),
    Product("MSC-6002", "Halyard Chemical Gauntlet Nitrile 33cm, Pair",
            "Halyard chemical resistant nitrile gauntlet, 33cm, EN374, size 9.",
            "ppe.hand.chem", "46181541", "PR", D("4.95"), "Halyard", "HY-CG-33-9", 3),
    Product("MSC-6010", "Halyard Safety Boot S3 Composite Toe",
            "Halyard S3 safety boot, composite toecap and midsole, water resistant "
            "leather upper, size 9.",
            "ppe.foot.boots", "46181604", "PR", D("58.00"), "Halyard", "HY-SB-S3-9",
            7, price_breaks=(PB(5, D("54.00")),), aux_token="SZ-9"),
    Product("MSC-6020", "Halyard Safety Spectacle Clear Anti-Fog",
            "Halyard clear polycarbonate safety spectacle, EN166 1FT, anti-fog and "
            "anti-scratch coated.",
            "ppe.head.eye", "46181802", "EA", D("3.45"), "Halyard", "HY-SS-CL",
            3, price_breaks=(PB(20, D("2.95")), PB(100, D("2.55")))),
    Product("MSC-6021", "Halyard Safety Helmet Vented White",
            "Halyard vented safety helmet, EN397, 6-point harness with wheel ratchet.",
            "ppe.head.hard", "46181701", "EA", D("11.20"), "Halyard", "HY-SH-V-WH", 3),
    Product("MSC-6022", "Halyard Ear Defender SNR 31dB",
            "Halyard over-ear defender, SNR 31dB, EN352-1, folding headband.",
            "ppe.head.ear", "46181902", "EA", D("13.60"), "Halyard", "HY-ED-31", 3),
    Product("MSC-6030", "Halyard FFP3 Valved Respirator, Box of 10",
            "Halyard FFP3 valved fold-flat respirator, EN149:2001+A1:2009. Box of 10.",
            "ppe.resp.masks", "46182002", "BX", D("28.50"), "Halyard", "HY-FFP3-V-10",
            5, pack_size=10),
    Product("MSC-6040", "Halyard Hi-Vis Vest Yellow Class 2",
            "Halyard hi-vis waistcoat, EN ISO 20471 Class 2, yellow, touch fastening.",
            "ppe.wear.hivis", "46181507", "EA", D("4.20"), "Halyard", "HY-HV-Y-L",
            3, price_breaks=(PB(25, D("3.60")),)),
    Product("MSC-6050", "Halyard First Aid Kit BS8599-1 Medium",
            "Halyard workplace first aid kit, BS 8599-1 compliant, medium, wall "
            "mountable case.",
            "ppe.first.kits", "42172001", "EA", D("31.90"), "Halyard", "HY-FA-M", 5),

    # --- Catering -----------------------------------------------------------
    Product("MSC-7000", "Meridian Paper Cup 12oz, Sleeve of 50",
            "Meridian single-wall paper hot cup, 12oz, plain white. Sleeve of 50.",
            "cater.disp.cups", "52151504", "PK", D("4.60"), "Meridian", "MER-PC-12-50",
            4, pack_size=50, price_breaks=(PB(20, D("4.10")),)),
    Product("MSC-7010", "Meridian Fairtrade Ground Coffee 1kg",
            "Meridian Fairtrade medium roast ground coffee, 1kg vacuum pack.",
            "cater.bev.hot", "50201706", "EA", D("16.80"), "Meridian", "MER-GC-1K", 5),
    Product("MSC-7011", "Meridian Everyday Tea Bags, Box of 1100",
            "Meridian everyday tea bags, one-cup. Box of 1100.",
            "cater.bev.hot", "50201713", "BX", D("28.40"), "Meridian", "MER-TB-1100",
            5, pack_size=1100),

    # --- Packaging ----------------------------------------------------------
    Product("MSC-8000", "Meridian Single Wall Carton 305x229x229mm, Pack of 25",
            "Meridian single wall corrugated carton, 305x229x229mm, brown. Pack of 25.",
            "pack.boxes.corrugated", "24121503", "PK", D("18.90"), "Meridian", "MER-SW-305-25",
            5, pack_size=25),
    Product("MSC-8010", "Meridian Bubble Wrap 500mm x 100m",
            "Meridian small bubble wrap roll, 500mm x 100m, 10mm bubble.",
            "pack.protect.bubble", "24141601", "EA", D("21.50"), "Meridian", "MER-BW-500", 5),
    Product("MSC-8020", "Meridian Vinyl Packaging Tape 48mm x 66m, Pack of 6",
            "Meridian low-noise vinyl packaging tape, 48mm x 66m, clear. Pack of 6.",
            "pack.wrap.tape", "31201517", "PK", D("11.20"), "Meridian", "MER-PT-48-6",
            3, pack_size=6, price_breaks=(PB(10, D("10.10")),)),

    # --- MRO ----------------------------------------------------------------
    Product("MSC-9000", "Ferrum Pozi Countersunk Screw 4x30mm, Box of 200",
            "Ferrum pozidriv countersunk woodscrew, 4.0x30mm, zinc plated. Box of 200.",
            "mro.fast.screws", "31161509", "BX", D("6.40"), "Ferrum", "FR-PZ-430-200",
            3, pack_size=200),
    Product("MSC-9001", "Ferrum Hex Bolt M10x50 A2 Stainless, Box of 50",
            "Ferrum hexagon head set screw, M10x50, A2 stainless steel, DIN 933. Box of 50.",
            "mro.fast.bolts", "31161620", "BX", D("18.75"), "Ferrum", "FR-HX-M1050-50",
            5, pack_size=50),
    Product("MSC-9002", "Ferrum Form A Washer M10 A2, Box of 200",
            "Ferrum Form A flat washer, M10, A2 stainless steel, DIN 125. Box of 200.",
            "mro.fast.bolts", "31161807", "BX", D("9.20"), "Ferrum", "FR-WA-M10-200",
            5, pack_size=200),
    Product("MSC-9010", "Ferrum VDE Screwdriver Set 7 Piece",
            "Ferrum VDE insulated screwdriver set, 7 piece, 1000V rated, slotted and "
            "pozidriv tips.",
            "mro.hand.drivers", "27111701", "SET", D("34.90"), "Ferrum", "FR-VDE-7", 5),
    Product("MSC-9011", "Ferrum Hex Key Set Metric 9 Piece",
            "Ferrum long arm ball-end hex key set, metric, 1.5-10mm, 9 piece.",
            "mro.hand.drivers", "27111710", "SET", D("12.60"), "Ferrum", "FR-HK-9", 5),
    Product("MSC-9012", "Ferrum Tape Measure 8m Class II",
            "Ferrum 8m/26ft tape measure, Class II accuracy, nylon coated blade, "
            "magnetic hook.",
            "mro.hand.measure", "27111801", "EA", D("11.40"), "Ferrum", "FR-TM-8", 5),
    Product("MSC-9020", "Ferrum 18V Brushless Combi Drill Kit",
            "Ferrum 18V brushless combi drill, 2x5.0Ah batteries, fast charger and "
            "carry case. 60Nm torque, 13mm chuck.",
            "mro.power.drills", "27112703", "EA", D("189.00"), "Ferrum", "FR-CD-18B",
            7, aux_token="CFG-CD18-2X50"),
    Product("MSC-9030", "Ferrum Alkaline Battery AA, Box of 40",
            "Ferrum AA alkaline battery, LR6 1.5V. Box of 40.",
            "mro.elec.batteries", "26111702", "BX", D("14.90"), "Ferrum", "FR-AA-40",
            3, pack_size=40, price_breaks=(PB(10, D("13.40")),)),
    Product("MSC-9040", "Lumen LED Tube 1500mm 22W 4000K, Case of 25",
            "Lumen T8 LED tube, 1500mm, 22W, 4000K neutral white, 3200lm. Case of 25.",
            "mro.elec.lamps", "32111503", "CS", D("142.50"), "Lumen", "LM-T8-15-22",
            7, pack_size=25),

    # --- Lab & clinical -----------------------------------------------------
    Product("MSC-9500", "Meridian Pipette Tip 200ul Natural, Rack of 960",
            "Meridian universal pipette tip, 200ul, natural, graduated. Rack of 960.",
            "lab.consum.pipette", "41121607", "PK", D("42.00"), "Meridian", "MER-PT-200-960",
            10, pack_size=960),
    Product("MSC-9510", "Meridian Borosilicate Beaker 250ml, Pack of 10",
            "Meridian borosilicate glass low-form beaker, 250ml, graduated with spout. "
            "Pack of 10.",
            "lab.consum.glass", "41121803", "PK", D("28.60"), "Meridian", "MER-BK-250-10",
            10, pack_size=10),
    Product("MSC-9511", "Meridian Centrifuge Tube 15ml Sterile, Bag of 50",
            "Meridian 15ml conical centrifuge tube, polypropylene, sterile, graduated. "
            "Bag of 50.",
            "lab.consum.glass", "41121703", "PK", D("16.40"), "Meridian", "MER-CT-15-50",
            10, pack_size=50),
    Product("MSC-9520", "Meridian Nitrile Exam Glove Blue M, Box of 100",
            "Meridian powder-free nitrile examination glove, blue, medium. "
            "AQL 1.5, EN455. Box of 100.",
            "lab.clinical.gloves", "42132203", "BX", D("7.80"), "Meridian", "MER-NX-B-M",
            3, pack_size=100, price_breaks=(PB(10, D("7.10")), PB(50, D("6.45")))),

    # ======================================================================= #
    # Cross-listed products. These exist to keep the UNSPSC<->category mapping
    # genuinely MANY-to-many (see models.py and CATEGORY_UNSPSC_SPREAD below).
    #
    # They are not padding: every one is a real merchandising decision a
    # supplier makes. Nitrile gloves are sold as both a clinical consumable and
    # a janitorial one; the same commodity code, two aisles, different buyers.
    # Without lines like these the tree collapses to one code per leaf, which
    # is the tell that a catalogue was generated rather than merchandised.
    # ======================================================================= #
    Product("MSC-5050", "Meridian Nitrile Disposable Glove Blue L, Box of 100",
            "Meridian powder-free nitrile disposable glove, blue, large. General "
            "janitorial and food-handling use. Box of 100.",
            "fac.equip.cloths", "42132203", "BX", D("7.40"), "Meridian", "MER-NX-B-L",
            3, pack_size=100),          # same code as MSC-9520 (clinical aisle)
    Product("MSC-9530", "Halyard Lab Safety Spectacle Clear Side-Shield",
            "Halyard clear polycarbonate laboratory safety spectacle with integral "
            "side shields, EN166 1F.",
            "lab.consum.glass", "46181802", "EA", D("4.10"), "Halyard", "HY-LS-CL",
            4),                          # same code as MSC-6020 (PPE aisle)
    Product("MSC-7020", "Brightwell Kitchen Roll 2-Ply, Pack of 4",
            "Brightwell 2-ply kitchen roll for breakroom and catering areas. Pack of 4.",
            "cater.disp.cups", "14111703", "PK", D("3.95"), "Brightwell", "BW-KR-4",
            4, pack_size=4),             # same code as MSC-5020 (facilities aisle)
    Product("MSC-3060", "Ferrum Alkaline Battery AAA, Box of 40",
            "Ferrum AAA alkaline battery, LR03 1.5V, for keyboards, mice and remote "
            "devices. Box of 40.",
            "it.input.keyboards", "26111702", "BX", D("15.40"), "Ferrum", "FR-AAA-40",
            3, pack_size=40),            # same code as MSC-9030 (MRO aisle)
    Product("MSC-2040", "Meridian A4 Presentation Paper 160gsm",
            "Meridian A4 white presentation paper, 160gsm, 250 sheets. Suitable for "
            "colour laser and inkjet output.",
            "print.devices.laser", "14111507", "RM", D("8.20"), "Meridian", "MER-A4-160",
            4),                          # same code as MSC-1001 (office aisle)
    Product("MSC-6060", "Brightwell Alcohol Hand Sanitiser 5L Refill",
            "Brightwell 70% alcohol hand sanitiser gel, 5 litre refill for wall "
            "dispensers. Site welfare and first aid points.",
            "ppe.first.kits", "53131626", "EA", D("21.80"), "Brightwell", "BW-HS-5L",
            4, hazardous=True),          # same code as MSC-5002 (facilities aisle)

    # ======================================================================= #
    # Deliberately imperfect products. See models.Quirk — every defect below
    # is a documented real-world failure, not an invented one. Do not "fix"
    # them; they are the inputs that exercise the normalisation and advisory
    # paths, and a catalogue cleaner than reality is a catalogue that teaches
    # people the wrong lessons.
    # ======================================================================= #
    Product("MSC-Q100", "Quillon Sticky Note 76x76mm Yellow",
            "Quillon repositionable sticky note, 76x76mm, yellow, 100 sheets per pad.",
            "office.paper.pads", "14111530", "EACH", D("0.85"), "Quillon", "QN-SN-76",
            2, quirks=(Q.SLOPPY_UOM,)),
    Product("MSC-Q101", "Halyard Disposable Apron White",
            "Halyard polythene disposable apron on a roll, white, 27x42in.",
            "ppe.wear.hivis", "46181503", "100/BX", D("9.40"), "Halyard", "HY-AP-100",
            3, pack_size=100, quirks=(Q.PACK_IN_UOM,)),
    Product("MSC-Q102", "Vantor ProBook 16 Mobile Workstation i9",
            "Vantor ProBook 16 mobile workstation featuring a 16-inch 120Hz display, "
            "Core i9 processor, 64GB DDR5 memory, 2TB NVMe solid state storage, "
            "discrete professional graphics with 8GB dedicated memory, Thunderbolt 4, "
            "Wi-Fi 6E, backlit keyboard with numeric keypad, fingerprint reader, "
            "infrared camera for facial recognition, 99Wh battery, and a comprehensive "
            "three year next business day on-site warranty with accidental damage cover.",
            "it.compute.laptops", "43211503", "EA", D("2489.00"), "Vantor", "VT-PB16-I9",
            21, aux_token="CFG-PB16-I9-64-2048-GFX8", quirks=(Q.LONG_DESCRIPTION,)),
    Product("MSC-Q103", "Ferrum Socket Set 1/2in Drive 24 Piece",
            "Ferrum 1/2in drive socket set, 24 piece, 10-32mm, chrome vanadium.",
            "mro.hand.drivers", "27111703", "SET", D("46.50"), "Ferrum", "FR-SS-12-24",
            5, quirks=(Q.DELIMITED_PART_ID,)),
    Product("MSC-Q104", "Brightwell Citron Dégraissant 5L",
            "Brightwell dégraissant citron — concentrated citrus degreaser, 5 litre. "
            "Idéal pour cuisines professionnelles. Dilution 1:50.",
            "fac.chem.general", "47131805", "EA", D("15.90"), "Brightwell", "BW-DG-5L",
            4, hazardous=True, quirks=(Q.NON_ASCII,)),
    Product("MSC-Q105", "Cartwright Executive Desk 1800x900 Configurable",
            "Cartwright executive desk, 1800x900mm, configurable finish, pedestal and "
            "cable management options.",
            "furn.desks.office", "56101703", "EA", D("612.00"), "Cartwright", "CW-DK-EX-1890",
            25,
            aux_token=("CFG:finish=walnut;edge=postform;pedestal=3drw-left;"
                       "cablemgmt=yes;grommet=twin;modesty=full;wireway=vertical;"
                       "leg=panel-end;delivery=installed;contract=CTR-FURN-2026-A"),
            quirks=(Q.LONG_AUX_ID,)),
    Product("MSC-Q106", "Ferrum Copper Crimp Terminal 6mm, Box of 100",
            "Ferrum tinned copper crimp ring terminal, 6mm stud, 4-6mm² cable. Box of 100.",
            "mro.elec.batteries", "26121536", "BX", D("11.4375"), "Ferrum", "FR-CT-6-100",
            4, pack_size=100, quirks=(Q.SUB_PENNY_PRICE,)),
    Product("MSC-Q107", "Marlowe Permanent Marker Black, Box of 12",
            "Marlowe bullet tip permanent marker, black, low odour. Box of 12.",
            "office.write.pencils", "44.12.17.08", "BX", D("7.90"), "Marlowe", "MW-PM-BK-12",
            2, pack_size=12, quirks=(Q.PUNCTUATED_UNSPSC,)),
)


# --------------------------------------------------------------------------- #
# Lookups
# --------------------------------------------------------------------------- #
BY_SKU: dict[str, Product] = {p.sku: p for p in PRODUCTS}
BY_CATEGORY: dict[str, list[Product]] = {}
for _p in PRODUCTS:
    BY_CATEGORY.setdefault(_p.category, []).append(_p)

CATEGORY_BY_ID: dict[str, Category] = {c.id: c for c in CATEGORIES}


def children_of(category_id: str | None) -> list[Category]:
    return [c for c in CATEGORIES if c.parent == category_id]


def ancestry(category_id: str) -> list[Category]:
    """Breadcrumb from top level down to the given category."""
    chain: list[Category] = []
    current = CATEGORY_BY_ID.get(category_id)
    while current is not None:
        chain.insert(0, current)
        current = CATEGORY_BY_ID.get(current.parent) if current.parent else None
    return chain


def products_in_tree(category_id: str) -> list[Product]:
    """Every product at or below a category — what a browse page shows."""
    wanted = {category_id}
    changed = True
    while changed:
        changed = False
        for cat in CATEGORIES:
            if cat.parent in wanted and cat.id not in wanted:
                wanted.add(cat.id)
                changed = True
    return [p for p in PRODUCTS if p.category in wanted]


def search(term: str) -> list[Product]:
    needle = term.strip().lower()
    if not needle:
        return []
    return [
        p for p in PRODUCTS
        if needle in p.name.lower()
        or needle in p.description.lower()
        or needle in p.sku.lower()
        or needle in p.manufacturer_part_id.lower()
    ]


# --------------------------------------------------------------------------- #
# The decoupling invariant
# --------------------------------------------------------------------------- #
# A UNSPSC code appearing under several leaf categories, and a leaf category
# spanning several UNSPSC codes, is the property that makes this read as a real
# catalogue (see models.py). It is also the property that silently decays into
# a 1:1 mapping as products are added, so it is measured rather than assumed.
CATEGORY_UNSPSC_SPREAD: dict[str, set[str]] = {}
for _p in PRODUCTS:
    CATEGORY_UNSPSC_SPREAD.setdefault(_p.category, set()).add(_p.unspsc)

UNSPSC_CATEGORY_SPREAD: dict[str, set[str]] = {}
for _p in PRODUCTS:
    UNSPSC_CATEGORY_SPREAD.setdefault(_p.unspsc, set()).add(_p.category)
