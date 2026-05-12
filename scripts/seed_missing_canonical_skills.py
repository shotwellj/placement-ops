"""Seed missing canonical skills surfaced by the Phase A engine on real data.

The top unresolved raw_skill_text in production are all in core embedded/
hardware/networking domains the existing 197-skill taxonomy doesn't cover:
TCP/IP, I2C, SPI, UART, PCB Design, Altium Designer, BSP, HAL, OrCAD,
MISRA, etc.

This script adds them as canonical skills with sensible aliases (so common
variants resolve automatically) and adjacencies (so partial matches still
score). Idempotent - safe to re-run.

After running, back-populate existing req_skills and candidate_skills rows
that have skill_id=NULL and a raw_skill_text matching the new canonical
or alias - they should auto-resolve on next read by resolve_skill_id, but
back-populating makes the engine see them immediately on next eval.
"""
import os, sys, json, uuid, httpx

env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env.production'))
env = {}
for line in open(env_path):
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line: continue
    k, _, v = line.partition('='); env[k.strip()] = v.strip().strip('"').strip("'")
url = env['TURSO_URL'].replace('libsql://', 'https://')
TOKEN = env['TURSO_AUTH_TOKEN']

def tquery(sql, args=None):
    typed_args = []
    for a in (args or []):
        if a is None:
            typed_args.append({"type": "null"})
        elif isinstance(a, bool):
            typed_args.append({"type": "integer", "value": "1" if a else "0"})
        elif isinstance(a, int):
            typed_args.append({"type": "integer", "value": str(a)})
        elif isinstance(a, float):
            typed_args.append({"type": "float", "value": a})
        else:
            typed_args.append({"type": "text", "value": str(a)})
    r = httpx.post(f'{url}/v2/pipeline',
        headers={'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'},
        json={'requests':[{'type':'execute','stmt':{'sql':sql, 'args': typed_args}},{'type':'close'}]},
        timeout=20)
    if r.status_code != 200:
        print(f"HTTP {r.status_code}: {r.text[:300]}")
        sys.exit(1)
    res = r.json()['results'][0]
    if res.get('type') == 'error':
        print(f"DB error: {res.get('error', {}).get('message')}")
        return None
    rows = res.get('response', {}).get('result', {}).get('rows', [])
    return [[c.get('value') for c in row] for row in rows]


# Skills to add. Each entry: (canonical_name, category, weight, aliases, adjacent_canonical)
# 'adjacent_canonical' is a list of OTHER canonical names this skill is adjacent to.
# The script will look them up after insertion to build adjacencies.
SKILLS = [
    # Serial protocols (core embedded)
    ("I2C", "serial_protocol", "high",
     ["I2C Protocol", "i2c protocol", "I²C", "I2C Bus"],
     ["SPI", "UART", "Device Drivers", "Embedded C", "Embedded Firmware"]),
    ("SPI", "serial_protocol", "high",
     ["SPI Protocol", "spi protocol", "SPI Bus", "Serial Peripheral Interface"],
     ["I2C", "UART", "Device Drivers", "Embedded C", "Embedded Firmware"]),
    ("UART", "serial_protocol", "high",
     ["UART Protocol", "uart protocol", "Serial Communication", "Serial UART"],
     ["I2C", "SPI", "Device Drivers", "Embedded C"]),

    # Networking
    ("TCP/IP", "networking_protocol", "high",
     ["TCP/IP Networking", "tcp/ip networking", "TCP/IP Stack", "TCP/IP Protocol"],
     ["Ethernet", "Network Protocols", "Wi-Fi"]),
    ("Ethernet", "networking_protocol", "high",
     ["Ethernet 1G/10G", "Ethernet Driver", "Gigabit Ethernet"],
     ["TCP/IP", "Network Protocols", "Device Drivers"]),
    ("Network Protocols", "networking_protocol", "high",
     ["Network Protocol Debugging", "Networking Protocols", "Protocol Stack"],
     ["TCP/IP", "Ethernet", "Wi-Fi"]),
    ("Wi-Fi", "networking_protocol", "medium",
     ["Wi-Fi Networking", "wi-fi networking", "Wireless Networking", "WiFi"],
     ["TCP/IP", "Network Protocols"]),

    # Hardware design / PCB
    ("PCB Design", "hardware_design", "high",
     ["PCB Layout", "pcb design", "Printed Circuit Board Design", "Board Design"],
     ["Altium Designer", "OrCAD", "Analog Circuit Design", "Digital Circuit Design"]),
    ("Altium Designer", "hardware_design", "high",
     ["altium designer", "Altium", "Altium Designer Software"],
     ["PCB Design", "OrCAD"]),
    ("OrCAD", "hardware_design", "high",
     ["orcad", "OrCAD PCB", "Cadence OrCAD"],
     ["PCB Design", "Altium Designer"]),
    ("Analog Circuit Design", "hardware_design", "high",
     ["analog circuit design", "Analog Design", "Analog Electronics"],
     ["Digital Circuit Design", "PCB Design"]),
    ("Digital Circuit Design", "hardware_design", "high",
     ["digital circuit design", "Digital Design", "Digital Electronics", "Digital Hardware Design"],
     ["Analog Circuit Design", "PCB Design", "FPGA Development"]),

    # Test/measurement equipment (core hardware engineer signal)
    ("Oscilloscope", "test_equipment", "medium",
     ["oscilloscope", "Oscilloscopes", "Scope"],
     ["Logic Analyzer", "JTAG", "Hardware Debugging"]),
    ("Logic Analyzer", "test_equipment", "medium",
     ["logic analyzer", "Logic Analyzers"],
     ["Oscilloscope", "JTAG", "Hardware Debugging"]),
    ("Hardware Debugging", "embedded_skill", "high",
     ["Hardware Troubleshooting", "Hardware Debug", "Bring-Up Debugging"],
     ["JTAG", "Oscilloscope", "Logic Analyzer", "Board Bring-Up"]),

    # Embedded platform skills
    ("Board Bring-Up", "embedded_skill", "high",
     ["Board Bring Up", "board bring-up", "Bring-up", "Hardware Bring-Up", "Board Bringup"],
     ["BSP Development", "Hardware Debugging", "JTAG", "Embedded C", "Device Drivers"]),
    ("BSP Development", "embedded_skill", "high",
     ["bsp development", "Board Support Package", "BSP Customization", "Board Support Package (BSP)"],
     ["Board Bring-Up", "Device Drivers", "Embedded Linux", "Linux Kernel"]),
    ("Embedded Linux", "embedded_skill", "high",
     ["Embedded Linux Development", "Linux Embedded"],
     ["Linux", "Linux Kernel", "BSP Development", "Yocto"]),
    ("Hardware Abstraction Layer", "embedded_skill", "medium",
     ["Hardware Abstraction Layer (HAL)", "HAL", "hardware abstraction layer (hal)"],
     ["Device Drivers", "Embedded C", "BSP Development"]),
    ("Yocto", "embedded_skill", "medium",
     ["Yocto Project", "yocto project", "Yocto Linux"],
     ["Embedded Linux", "BSP Development", "Linux Kernel"]),
    ("Embedded Systems Design", "embedded_skill", "high",
     ["embedded systems design", "Embedded Systems", "Embedded System Architecture"],
     ["Embedded C", "Embedded Firmware", "RTOS", "Board Bring-Up"]),

    # MCU/SoC platforms
    ("ARM Cortex", "mcu_platform", "high",
     ["ARM Cortex-M", "ARM Cortex-A", "Cortex-M", "Cortex-A"],
     ["ARM", "Embedded C", "RTOS"]),
    ("Zynq", "mcu_platform", "medium",
     ["Zynq UltraScale+", "Zynq 7000", "Zynq MPSoC", "Xilinx Zynq", "Zynq FPGA/SoC"],
     ["ARM Cortex", "FPGA Development", "ARM"]),

    # Coding standards / quality
    ("MISRA C", "code_quality", "medium",
     ["MISRA C/C++", "misra c/c++", "MISRA", "MISRA Compliance"],
     ["Embedded C", "C++", "Safety-Critical Coding"]),
    ("Test-Driven Development", "engineering_practice", "medium",
     ["TDD", "test-driven development", "Test Driven Development"],
     ["Unit Testing", "Embedded C", "C++"]),
    ("Unit Testing", "engineering_practice", "medium",
     ["unit testing", "Unit Tests"],
     ["Test-Driven Development"]),

    # Scripting / tooling
    ("Bash Scripting", "scripting", "medium",
     ["Bash", "Shell Scripting", "bash/shell scripting", "Bash/Shell Scripting"],
     ["Linux", "DevOps"]),

    # Other commonly missed
    ("Power Management", "embedded_skill", "medium",
     ["Power Management Firmware", "Power Management Software", "power management firmware"],
     ["Embedded C", "Embedded Firmware"]),
    ("Thermal Management", "embedded_skill", "low",
     ["thermal management", "Thermal Design", "Thermal Analysis"],
     ["Hardware Debugging", "PCB Design"]),
    ("System Architecture", "engineering_practice", "high",
     ["system architecture", "System Design", "Architecture Design"],
     ["Technical Leadership"]),
    ("Technical Leadership", "soft_skill", "high",
     ["technical leadership", "Tech Lead", "Engineering Leadership"],
     ["System Architecture"]),
    ("Technical Writing", "soft_skill", "medium",
     ["technical writing", "Technical Documentation", "Design Documentation"],
     []),
    ("Real-Time Systems", "embedded_skill", "high",
     ["Real-Time", "Real Time Systems", "Real-Time Operating Systems (RTOS)", "real-time operating systems (rtos)"],
     ["RTOS", "Embedded Firmware"]),
]

# Step 1: insert canonical skills + aliases (idempotent)
print(f"=== Phase 1: Adding {len(SKILLS)} canonical skills ===")
added_canonical = 0
skipped = 0
canonical_to_id = {}

for canonical_name, category, weight, aliases, adjacent_to in SKILLS:
    # Check if already exists
    existing = tquery("SELECT id FROM skills WHERE LOWER(canonical_name) = LOWER(?)",
                       [canonical_name])
    if existing and existing[0]:
        canonical_to_id[canonical_name] = existing[0][0]
        skipped += 1
        continue

    sid = "sk_" + uuid.uuid4().hex[:16]
    aliases_json = json.dumps(aliases) if aliases else None
    tquery(
        """INSERT INTO skills (id, canonical_name, category, aliases_json, weight)
           VALUES (?, ?, ?, ?, ?)""",
        [sid, canonical_name, category, aliases_json, weight],
    )
    canonical_to_id[canonical_name] = sid
    added_canonical += 1
    print(f"  + {canonical_name} ({len(aliases)} aliases)")

print(f"\n  Added {added_canonical} new canonical skills, skipped {skipped} existing")

# Step 2: build adjacencies. We need both sides of each pair resolved.
print(f"\n=== Phase 2: Adding adjacencies ===")
# Pre-load ALL canonical skills now so we can resolve adjacent_to names that
# point to existing skills
all_canonical = tquery("SELECT id, canonical_name FROM skills")
name_to_id = {r[1]: r[0] for r in all_canonical}

added_adj = 0
skipped_adj = 0
for canonical_name, _, _, _, adjacent_to in SKILLS:
    src_id = name_to_id.get(canonical_name)
    if not src_id:
        continue
    for adj_name in adjacent_to:
        adj_id = name_to_id.get(adj_name)
        if not adj_id:
            print(f"  ? adjacency target not in taxonomy: {adj_name}")
            continue
        if src_id == adj_id:
            continue
        # Check if adjacency already exists (either direction)
        existing = tquery(
            """SELECT id FROM skill_adjacencies
               WHERE (skill_id = ? AND adjacent_id = ?) OR (skill_id = ? AND adjacent_id = ?)""",
            [src_id, adj_id, adj_id, src_id],
        )
        if existing and existing[0]:
            skipped_adj += 1
            continue
        adj_row_id = "adj_" + uuid.uuid4().hex[:16]
        tquery(
            """INSERT INTO skill_adjacencies (id, skill_id, adjacent_id, weight, source)
               VALUES (?, ?, ?, ?, ?)""",
            [adj_row_id, src_id, adj_id, 0.6, "taxonomy"],
        )
        added_adj += 1

print(f"  Added {added_adj} adjacencies, skipped {skipped_adj} existing")

# Step 3: back-populate existing req_skills + candidate_skills with skill_id=NULL
print(f"\n=== Phase 3: Back-populating unresolved rows ===")
# For each new skill, find any req_skills.raw_skill_text or candidate_skills.raw_skill_text
# that LOWER-matches the canonical_name or any alias, and UPDATE skill_id.
backfilled_req = 0
backfilled_cand = 0
for canonical_name, _, _, aliases, _ in SKILLS:
    sid = name_to_id.get(canonical_name)
    if not sid:
        continue
    candidates_to_match = [canonical_name.lower()] + [a.lower() for a in aliases]
    for txt in candidates_to_match:
        # req_skills
        rs = tquery(
            "UPDATE req_skills SET skill_id = ? WHERE skill_id IS NULL AND LOWER(raw_skill_text) = ? RETURNING id",
            [sid, txt],
        )
        if rs:
            backfilled_req += len(rs)
        # candidate_skills
        rs = tquery(
            "UPDATE candidate_skills SET skill_id = ? WHERE skill_id IS NULL AND LOWER(raw_skill_text) = ? RETURNING id",
            [sid, txt],
        )
        if rs:
            backfilled_cand += len(rs)

print(f"  Back-populated {backfilled_req} req_skills + {backfilled_cand} candidate_skills")

# Step 4: final status
print(f"\n=== Final status ===")
counts = tquery("""SELECT
    (SELECT COUNT(*) FROM skills) as skills,
    (SELECT COUNT(*) FROM skill_adjacencies) as adjacencies,
    (SELECT COUNT(*) FROM req_skills WHERE skill_id IS NOT NULL) as resolved_req,
    (SELECT COUNT(*) FROM req_skills) as total_req,
    (SELECT COUNT(*) FROM candidate_skills WHERE skill_id IS NOT NULL) as resolved_cand,
    (SELECT COUNT(*) FROM candidate_skills) as total_cand""")
if counts and counts[0]:
    s, a, rr, tr, rc, tc = counts[0]
    print(f"  Skills:        {s}")
    print(f"  Adjacencies:   {a}")
    print(f"  Req skills:    {rr}/{tr} resolved ({int(int(rr)/int(tr)*100)}%)")
    print(f"  Cand skills:   {rc}/{tc} resolved ({int(int(rc)/int(tc)*100)}%)")
