namespace FloorWorks.FoundryIq;

public record Chunk(string Id, string Pillar, string Title, string Content, string Source, string? Asset = null, string? Part = null, string? Bulletin = null);

// Synthetic knowledge corpus generated from the Dataverse seed data (dataverse\provision-dataverse.ps1).
// Keep the Eq/Pt/Al tables in sync with that script so names and numbers stay consistent.
public static class Corpus
{
    record Eq(string Name, string Asset, string Plant, string Make, string Kind, string Install, string Owner,
        string Hazard, string Loto, string PmCycle, string FailureMode, string Priority, string Why, string[] Steps, string PartNo);
    record Pt(string Name, string No, string Supplier, string Category, decimal Cost, string Spec, string Install, string Inspect, string Eq);
    record Al(string Sb, string Title, string Sev, string Status, string Desc, int Defects, string Detected, string PartNo, string Part, string Asset, string Eq,
        string Cause, string Action, string Ticket, string Tech);

    static readonly Eq[] Equipment =
    {
        new("CNC Mill 3","EQ-1003","Grand Rapids, MI","Haas","3-axis vertical machining center","2021-03-15","Abby Smith",
            "rotating spindle, ejected chips, coolant mist","Lock out main disconnect and the spindle drive; wait 5 minutes for drive capacitors; verify zero spindle speed.","every 500 spindle hours (bearing runout check) and quarterly (coolant, way lube)",
            "Surface finish degradation and spindle noise","Normal","Finish defects scrap parts but the machine is still usable; escalate to High if runout exceeds 8 microns or a tool crash occurred.",
            new[]{"Measure spindle runout with a test bar and dial indicator (limit 5 microns).","Check spindle temperature rise after a 20 minute warm-up cycle.","Listen for bearing whine at 8000 rpm.","Inspect coolant concentration and drawbar clamp force."},"P-SB45"),
        new("Injection Press 2","EQ-2002","Monterrey, MX","Engel","300-ton hydraulic injection molding press","2019-09-01","Abby Smith",
            "hydraulic pressure up to 2500 psi, hot barrel, moving platens","Lock out the main power and hydraulic pump motor, release accumulator pressure via the bleed valve, block the platens with the mold safety bar.","every 1000 hours (hydraulic oil and filters) and quarterly (coolant, nozzle heaters)",
            "Hydraulic pressure drift during the hold phase","Normal","Drift of up to 5 percent is a quality risk, not a safety risk; raise to High if drift exceeds 10 percent or shot weight is out of tolerance.",
            new[]{"Check hydraulic oil level, temperature and contamination.","Compare the hold-pressure setpoint with the gauge reading during a cycle.","Inspect the proportional valve and check valve for internal leakage.","Verify coolant concentration (CC-5 refractometer reading)."},"P-CC5"),
        new("Robotic Welder R7","EQ-3007","Grand Rapids, MI","FANUC","6-axis robotic MIG welding cell","2022-06-20","John Brown",
            "arc flash, weld fumes, robot arm motion, pinch points","Lock out the robot controller and welding power source, verify the light curtain is armed, engage the cell gate interlock before entering.","every 2000 hours (reducer grease, cable check) and monthly (torch, wire feeder)",
            "Encoder position error and out-of-tolerance weld seams","High","Seam defects affect product quality and the cell can drift further; High unless the robot faults, then Urgent.",
            new[]{"Review the SRVO alarm history on the pendant.","Re-home the encoder and run mastering verification.","Run seam validation coupons and measure offset.","Inspect the servo motor cable and connector for damage."},"P-SM90"),
        new("Packaging Line A","EQ-4001","Brussels, BE","Bosch Packaging","high-speed cartoning and packaging line","2020-01-10","Aaron Minde",
            "pinch points, conveyor entanglement, compressed air","Lock out the line main disconnect and pneumatic supply; tag each conveyor drive; verify zero energy at the sealing jaws.","quarterly (torque audit, belts, sealing jaws) and annually (gearbox oil)",
            "Fasteners loosening or snapping during assembly torque","Normal","Fastener failures stop the line but are low risk; High if a structural bolt on a guard or drive fails.",
            new[]{"Audit torque on all M12 fasteners against the torque chart.","Inspect failed bolts for fracture surface (snap vs fatigue).","Quarantine fasteners from the suspect supplier lot.","Re-torque to specification with a calibrated wrench."},"P-BLT12"),
        new("Hydraulic Press H1","EQ-5001","Monterrey, MX","Schuler","400-ton hydraulic forming press","2018-11-05","John Brown",
            "stored hydraulic energy up to 3000 psi, crush hazard, high-pressure fluid injection","Lock out main power and the pump motor, release and verify zero pressure at all accumulators, install ram safety blocks, tag the valve manifold.","every 1000 hours (fluid, filters, valve seats) and annually (pressure vessel inspection)",
            "Hydraulic leak at valve seat or loss of tonnage","Urgent","Press down with leaking pressure components; fluid injection hazard and production stop, so Urgent.",
            new[]{"Depressurize and lock out before any inspection.","Identify the leaking valve by part number and lot marking.","Check the lot against open service bulletins before ordering parts.","Replace the valve with a part from a cleared lot and pressure-test to 3000 psi."},"P-HV220"),
        new("Heat Treat Furnace F2","EQ-6002","Brussels, BE","Ipsen","vacuum heat treat batch furnace","2023-02-28","Jamie Fergison",
            "temperatures above 1000 C, electrical hazards, quench gas","Lock out main power and the heater supply, allow the hot zone to cool below 60 C, verify with a thermocouple; purge quench gas before entry.","every 500 cycles (element and insulation check) and quarterly (thermocouple calibration)",
            "Heating element failures and zone temperature imbalance","High","Failed elements cause out-of-spec heat treat batches; High, Urgent if a batch is in process.",
            new[]{"Measure resistance on every element bank against the baseline.","Inspect element hangers, insulators and terminals for arcing.","Review the zone temperature uniformity survey.","Check furnace power supply and SCR firing for imbalance."},"P-HE12"),
    };

    static readonly Pt[] Parts =
    {
        new("Hydraulic Valve HV-220","P-HV220","Parker Hannifin","Hydraulic",184.50m,"Directional control valve, 3000 psi rated, seat seal in fluorocarbon, 24 VDC solenoid.","Depressurize, tag out, replace seal kit and O-rings, torque manifold bolts to 45 Nm, pressure-test to 3000 psi.","Check seat for extrusion marks and verify no leakage at rated pressure for 5 minutes.","Hydraulic Press H1 (EQ-5001)"),
        new("Servo Motor SM-90","P-SM90","Siemens","Electrical",920.00m,"AC servo motor with absolute encoder, 9 Nm continuous torque.","Remove power, tag out, disconnect encoder and power cable, mount with alignment dowels, re-master the robot axis.","Check encoder cable shielding, connector pins and position error trend.","Robotic Welder R7 (EQ-3007)"),
        new("Spindle Bearing SB-45","P-SB45","SKF","Mechanical",76.25m,"Angular contact spindle bearing pair, ABEC-7, 45 mm bore.","Replace as a matched set, apply specified preload, run a 2 hour break-in at stepped speeds.","Measure runout (limit 5 microns) and temperature rise after warm-up.","CNC Mill 3 (EQ-1003)"),
        new("Heating Element HE-12","P-HE12","Watlow","Electrical",310.00m,"Graphite heating element, 12 kW per bank, rated 1300 C.","Cool below 60 C, lock out, replace the full bank if resistance varies more than 5 percent, torque terminals to 8 Nm.","Resistance check per bank; inspect for hot spots and terminal arcing.","Heat Treat Furnace F2 (EQ-6002)"),
        new("Hex Bolt M12 Grade 10.9","P-BLT12","Fastenal","Fastener",0.42m,"M12 x 1.75 hex bolt, grade 10.9, zinc flake coated, torque 110 Nm.","Torque to 110 Nm with a calibrated wrench; do not reuse torque-to-yield bolts.","Inspect for necking, thread damage and coating defects.","Packaging Line A (EQ-4001)"),
        new("Coolant Concentrate CC-5","P-CC5","Castrol","Consumable",58.00m,"Semi-synthetic coolant concentrate, target mix 8 percent, refractometer reading 8 Brix.","Mix per target ratio, top up weekly, replace sump quarterly.","Check concentration with a refractometer and pH weekly.","Injection Press 2 (EQ-2002)"),
    };

    static readonly Al[] Alerts =
    {
        new("SB-2026-001","HV-220 valves leaking at seat","Critical","Investigating","Lot 4417 of HV-220 failing pressure test at 3000 psi; seat seal extrusion suspected.",38,"2026-10-01","P-HV220","HV-220","EQ-5001","Hydraulic Press H1",
            "Seat seal extrusion under pressure in lot 4417 due to out-of-spec seal hardness.","Quarantine lot 4417, do not install. Use stock from a cleared lot and pressure-test every replacement valve to 3000 psi.","Replace leaking HV-220 valve on press H1","Dana Whitfield"),
        new("SB-2026-002","Servo motor encoder drift","High","New","Intermittent encoder position error on robotic welder; 3 weld seams out of tolerance.",12,"2026-10-03","P-SM90","SM-90","EQ-3007","Robotic Welder R7",
            "Encoder signal drift from cable shield damage and loose encoder connector.","Re-home and master the axis, inspect the encoder cable, replace the SM-90 if drift recurs.","Recalibrate welder R7 encoder","Luis Ortega"),
        new("SB-2026-003","Spindle bearing premature wear","Medium","Contained","Surface finish degradation on CNC Mill 3; bearing runout above 8 microns.",25,"2026-09-27","P-SB45","SB-45","EQ-1003","CNC Mill 3",
            "Premature wear from insufficient preload and coolant ingress at the spindle nose.","Replace SB-45 as a matched set, verify preload, inspect spindle nose seals.","Swap spindle bearing on CNC Mill 3","Priya Nair"),
        new("SB-2026-004","Heating element burnout trend","High","Investigating","Three HE-12 elements failed within 6 weeks (expected life 12 months).",3,"2026-09-30","P-HE12","HE-12","EQ-6002","Heat Treat Furnace F2",
            "Early burnout from terminal arcing and contamination of the hot zone.","Resistance-check all banks, clean the hot zone, replace elements outside 5 percent of baseline.","Inspect furnace F2 heating elements","Marc Dubois"),
        new("SB-2026-005","Bolt torque-to-yield failures","Medium","New","M12 bolts snapping during assembly torque; supplier lot suspected.",60,"2026-10-05","P-BLT12","BLT12","EQ-4001","Packaging Line A",
            "Supplier lot with excess hardness causing brittle torque-to-yield fractures.","Quarantine the suspect lot, re-torque the line with fasteners from a cleared lot, run a torque audit.","Packaging line A preventive maintenance","Sofie Peeters"),
        new("SB-2026-006","Coolant concentration out of spec","Low","Closed","Refractometer readings 2 percent below spec; mix ratio corrected.",0,"2026-09-20","P-CC5","CC-5","EQ-2002","Injection Press 2",
            "Mix ratio error at the dosing unit.","Mix ratio corrected; recheck weekly with the refractometer. No parts action required.","Injection press 2 hydraulic pressure drift","Carlos Mendez"),
    };

    public static List<Chunk> Build()
    {
        var c = new List<Chunk>();
        foreach (var e in Equipment)
        {
            var src = $"Equipment Manual {e.Asset}";
            c.Add(new($"man-{e.Asset}-overview", "manual", $"{e.Name} ({e.Asset}) overview", $"{e.Name}, asset {e.Asset}, is a {e.Kind} made by {e.Make}, installed {e.Install} at {e.Plant}. Owner: {e.Owner}. Principal hazards: {e.Hazard}.", src, e.Asset));
            c.Add(new($"man-{e.Asset}-pm", "manual", $"{e.Name} ({e.Asset}) preventive maintenance", $"Preventive maintenance for {e.Name} ({e.Asset}): {e.PmCycle}. Record findings in the service ticket notes. Key consumable or wear part: {e.PartNo}.", src, e.Asset, e.PartNo));
            c.Add(new($"man-{e.Asset}-loto", "manual", $"{e.Name} ({e.Asset}) safety and LOTO", $"SAFETY - lockout/tagout for {e.Name} ({e.Asset}). Hazards: {e.Hazard}. Procedure: {e.Loto} Only authorized technicians may perform this procedure. Never bypass interlocks.", src, e.Asset));
            c.Add(new($"man-{e.Asset}-failure", "manual", $"{e.Name} ({e.Asset}) common failure modes", $"Common failure mode on {e.Name} ({e.Asset}): {e.FailureMode}. Primary part involved: {e.PartNo}. Typical ticket priority: {e.Priority}. Reason: {e.Why}", src, e.Asset, e.PartNo));
            c.Add(new($"play-{e.Asset}", "playbook", $"Diagnostic playbook: {e.FailureMode} on {e.Name}", $"Diagnostic playbook for {e.FailureMode} on {e.Name} ({e.Asset}). Typical priority {e.Priority}: {e.Why} Steps: 1) {e.Steps[0]} 2) {e.Steps[1]} 3) {e.Steps[2]} 4) {e.Steps[3]} Observe LOTO: {e.Loto}", $"Playbook {e.Asset}", e.Asset, e.PartNo));
            c.Add(new($"play-{e.Asset}-escalate", "playbook", $"Escalation guidance for {e.Name}", $"Escalate {e.Name} ({e.Asset}) tickets to the plant maintenance lead if the machine is down, a safety interlock is affected, or the same failure recurs within 30 days. Default priority for {e.FailureMode}: {e.Priority}.", $"Playbook {e.Asset}", e.Asset));
        }
        foreach (var p in Parts)
        {
            var src = $"Part Sheet {p.No}";
            c.Add(new($"part-{p.No}-sheet", "part", $"{p.Name} ({p.No}) part sheet", $"{p.Name}, part number {p.No}, supplier {p.Supplier}, category {p.Category}. Unit cost ${p.Cost:0.00}. {p.Spec} Used on {p.Eq}.", src, null, p.No));
            c.Add(new($"part-{p.No}-service", "part", $"{p.Name} ({p.No}) installation and inspection", $"Installation of {p.Name} ({p.No}): {p.Install} Inspection: {p.Inspect} Unit cost ${p.Cost:0.00} ({p.Supplier}).", src, null, p.No));
        }
        foreach (var a in Alerts)
        {
            var closed = a.Status == "Closed";
            c.Add(new($"sb-{a.Sb}", "bulletin", $"{a.Sb}: {a.Title}", $"SERVICE BULLETIN {a.Sb}: {a.Title}. Severity {a.Sev}, status {a.Status}, detected {a.Detected}. {a.Desc} Affected part: {a.Part} ({a.PartNo}); affected equipment: {a.Eq} ({a.Asset}); defect count {a.Defects}. Cause: {a.Cause} Required action: {a.Action} Related ticket: {a.Ticket} (technician {a.Tech}).", $"Service Bulletin {a.Sb}", a.Asset, a.PartNo, a.Sb));
            c.Add(new($"sb-{a.Sb}-impact", "bulletin", $"{a.Sb} ticket impact", $"If a caller reports an issue with {a.Eq} ({a.Asset}) related to {a.Part}, reference {a.Sb} ({a.Title}). Suggested priority: {(a.Sev is "Critical" ? "Urgent" : a.Sev)}. {(closed ? "This bulletin is closed; treat as informational." : "This bulletin is open; check the lot and quarantine status before ordering parts.")}", $"Service Bulletin {a.Sb}", a.Asset, a.PartNo, a.Sb));
            c.Add(new($"war-{a.Sb}", "warranty", $"Warranty and recall: {a.Part} ({a.PartNo})", a.Sb == "SB-2026-001"
                ? $"Recall notice: HV-220 valves from lot 4417 are subject to supplier recall by Parker Hannifin. Replacements under warranty are free of charge; return the quarantined valves with the lot marking. Reference {a.Sb}."
                : $"Warranty for {a.Part} ({a.PartNo}): supplier warranty covers manufacturing defects for 12 months from install. {(closed ? "No recall is active." : $"Report defects against {a.Sb}; the supplier may issue a credit if a lot defect is confirmed.")}", $"Warranty {a.PartNo}", a.Asset, a.PartNo, a.Sb));
        }
        return c;
    }
}
