// @category Biko3
// Bounded FDE-driven static analysis. No automatic whole-image analysis.
import ghidra.app.script.GhidraScript;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.SourceType;
import com.google.gson.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;

public class Biko3Focus extends GhidraScript {
    private String hex(long n) { return "0x" + Long.toHexString(n); }
    private String hash(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 3 || args.length > 43) throw new IllegalArgumentException("output boundaries-json original-nro [address ...]");
        Path output = Path.of(args[0]).toAbsolutePath();
        Files.createDirectories(output.resolve("decompiled"));
        byte[] boundaryBytes = Files.readAllBytes(Path.of(args[1]));
        byte[] nroBytes = Files.readAllBytes(Path.of(args[2]));
        String nroHash = hash(nroBytes);
        JsonObject boundaries = JsonParser.parseString(new String(boundaryBytes, StandardCharsets.UTF_8)).getAsJsonObject();
        if (!nroHash.equals(boundaries.get("source_nro_sha256").getAsString()))
            throw new IllegalArgumentException("FDE evidence NRO SHA mismatch");
        int ehStart = boundaries.get("eh_frame_start").getAsInt();
        int ehEnd = boundaries.get("eh_frame_end").getAsInt();
        if (!hash(Arrays.copyOfRange(nroBytes, ehStart, ehEnd)).equals(boundaries.get("eh_frame_sha256").getAsString()))
            throw new IllegalArgumentException("FDE evidence range SHA mismatch");
        Map<Long, Long> ranges = new TreeMap<>();
        for (JsonElement entry : boundaries.getAsJsonArray("functions")) {
            JsonObject f = entry.getAsJsonObject();
            long start = f.get("start").getAsLong(), size = f.get("size").getAsLong();
            if (start < 0x80 || size <= 0 || start + size > nroBytes.length)
                throw new IllegalArgumentException("invalid FDE function range");
            Long previous = ranges.putIfAbsent(start, size);
            if (previous != null && previous != size) throw new IllegalArgumentException("conflicting FDE ranges");
        }
        FunctionManager manager = currentProgram.getFunctionManager();
        List<Address> oldEntries = new ArrayList<>();
        FunctionIterator old = manager.getFunctions(true);
        while (old.hasNext()) oldEntries.add(old.next().getEntryPoint());
        for (Address entry : oldEntries) manager.removeFunction(entry);
        List<Map<String,Object>> seedFailures = new ArrayList<>();
        int seeded = 0;
        for (Map.Entry<Long,Long> entry : ranges.entrySet()) {
            monitor.checkCancelled();
            long start = entry.getKey(), size = entry.getValue();
            try {
                Address address = toAddr(start);
                manager.createFunction("fde_" + Long.toHexString(start), address,
                    new AddressSet(address, toAddr(start + size - 1)), SourceType.IMPORTED);
                seeded++;
            } catch (Exception e) {
                seedFailures.add(Map.of("entry", hex(start), "diagnostic", e.toString()));
            }
        }
        println("Seeded " + seeded + " FDE definitions; no automatic analysis requested");
        // This is a disassembly window, deliberately not a fabricated function.
        AddressSet entryWindow = new AddressSet(toAddr(0x80), toAddr(0x12f));
        DisassembleCommand entryCommand = new DisassembleCommand(toAddr(0x80), entryWindow, true);
        boolean entryDecoded = entryCommand.applyTo(currentProgram, monitor);
        StringBuilder entryAssembly = new StringBuilder("# Bounded entry window [0x80,0x130); no asserted function boundary\n");
        InstructionIterator entryInstructions = currentProgram.getListing().getInstructions(entryWindow, true);
        int entryCount = 0;
        while (entryInstructions.hasNext()) {
            Instruction instruction = entryInstructions.next();
            entryAssembly.append(hex(instruction.getAddress().getOffset())).append("  ")
                .append(instruction.toString()).append('\n'); entryCount++;
        }
        Files.writeString(output.resolve("entry-window.asm"), entryAssembly.toString());
        long[] targets = {0x5102f0L, 0x510700L, 0x99a90L, 0xdb3e0L, 0xdb480L, 0xf66f0L, 0x4fa210L};
        String[] names = {"candidate___nx_dynamic", "candidate___libnx_init", "candidate_main",
            "candidate_vkGetDeviceProcAddr", "candidate_vkGetInstanceProcAddr", "candidate_f66f0", "candidate_4fa210"};
        if (args.length > 3) {
            // Windows batch launchers may split commas into separate arguments.
            String[] requested = String.join(",", Arrays.copyOfRange(args, 3, args.length)).split(",");
            if (requested.length < 1 || requested.length > 40) throw new IllegalArgumentException("request 1-40 targets");
            targets = new long[requested.length]; names = new String[requested.length];
            for (int i = 0; i < requested.length; i++) {
                targets[i] = Long.decode(requested[i].trim());
                names[i] = "candidate_" + Long.toHexString(targets[i]);
            }
        }
        List<Map<String,Object>> results = new ArrayList<>();
        DecompInterface decompiler = new DecompInterface();
        try {
            if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException(decompiler.getLastMessage());
            for (int i = 0; i < targets.length; i++) {
                monitor.checkCancelled();
                long start = targets[i];
                Map<String,Object> item = new LinkedHashMap<>();
                item.put("entry", hex(start)); item.put("label", names[i]);
                results.add(item);
                try {
                    Long size = ranges.get(start);
                    if (size == null) throw new IllegalArgumentException("target has no exact FDE range");
                    Function f = manager.getFunctionAt(toAddr(start));
                    if (f == null) throw new IllegalArgumentException("target definition was not created");
                    f.setName(names[i], SourceType.USER_DEFINED);
                    item.put("body_bytes", size);
                    byte[] body = new byte[size.intValue()];
                    currentProgram.getMemory().getBytes(toAddr(start), body);
                    if (!Arrays.equals(body, Arrays.copyOfRange(nroBytes, (int)start, (int)(start + size))))
                        throw new IllegalArgumentException("ELF bytes differ from original NRO");
                    item.put("original_bytes_sha256", hash(body));
                    DisassembleCommand command = new DisassembleCommand(toAddr(start), f.getBody(), true);
                    item.put("disassembled", command.applyTo(currentProgram, monitor));
                    item.put("disassembly_diagnostic", command.getStatusMsg());
                    StringBuilder assembly = new StringBuilder();
                    InstructionIterator instructions = currentProgram.getListing().getInstructions(f.getBody(), true);
                    int count = 0;
                    List<Map<String,String>> tailOverrides = new ArrayList<>();
                    while (instructions.hasNext()) {
                        Instruction instruction = instructions.next();
                        assembly.append(hex(instruction.getAddress().getOffset())).append("  ")
                            .append(instruction.toString()).append('\n'); count++;
                        Address[] flows = instruction.getFlows();
                        if (instruction.getFlowType().isJump() && !instruction.getFlowType().isConditional()
                                && flows.length == 1 && !f.getBody().contains(flows[0])) {
                            tailOverrides.add(Map.of("site", hex(instruction.getAddress().getOffset()),
                                "destination", hex(flows[0].getOffset()), "analysis_override", "CALL_RETURN"));
                            // Bound the decompiler's native tail-flow traversal. The raw
                            // instruction bytes remain unchanged; the inference is recorded.
                            instruction.setFlowOverride(FlowOverride.CALL_RETURN);
                        }
                    }
                    item.put("instruction_count", count);
                    item.put("external_tail_flow_overrides", tailOverrides);
                    Files.writeString(output.resolve("decompiled/" + Long.toHexString(start) + ".asm"), assembly.toString());
                    println("Decompiling bounded target " + hex(start));
                    decompiler.flushCache();
                    DecompileResults result = decompiler.decompileFunction(f, 60, monitor);
                    item.put("completed", result.decompileCompleted());
                    item.put("diagnostic", result.getErrorMessage());
                    if (result.decompileCompleted() && result.getDecompiledFunction() != null) {
                        String file = "decompiled/" + Long.toHexString(start) + ".c";
                        String code = "/* Standalone Ghidra pseudocode; candidate labels; not original source. */\n" + result.getDecompiledFunction().getC();
                        Files.writeString(output.resolve(file), code, StandardCharsets.UTF_8);
                        item.put("file", file); item.put("pseudocode_sha256", hash(code.getBytes(StandardCharsets.UTF_8)));
                    }
                } catch (Exception error) {
                    item.put("completed", false); item.put("diagnostic", error.toString());
                }
            }
        } finally { decompiler.dispose(); }
        Map<String,Object> report = new LinkedHashMap<>();
        report.put("schema", 1); report.put("observed_at", Instant.now().toString());
        report.put("authority", "standalone-ghidra-bounded-fde-analysis");
        report.put("executable_sha256", currentProgram.getExecutableSHA256());
        report.put("source_nro_sha256", nroHash); report.put("boundary_json_sha256", hash(boundaryBytes));
        report.put("image_base", hex(currentProgram.getImageBase().getOffset()));
        report.put("language", currentProgram.getLanguageID().toString());
        report.put("cancelled", monitor.isCancelled()); report.put("seeded_fde_definitions", seeded);
        report.put("seed_failures", seedFailures); report.put("decompilation", results);
        report.put("entry_window", Map.of("start", "0x80", "end_exclusive", "0x130", "is_function", false,
            "disassembled", entryDecoded, "instruction_count", entryCount, "file", "entry-window.asm",
            "original_bytes_sha256", hash(Arrays.copyOfRange(nroBytes, 0x80, 0x130))));
        report.put("limitations", List.of("No automatic whole-image analysis; only selected FDE bodies and an explicit entry window disassembled.",
            "Function names are candidate labels, not recovered symbols.", "FDE records do not cover every function.",
            "External unconditional branches are recorded as analysis CALL_RETURN overrides to bound native decompiler traversal; this is a tail-call inference, not a byte patch.",
            "No game execution, runtime validation, or REA MCP evidence.", "Pseudocode is not original source or asserted compilable."));
        Files.writeString(output.resolve("functions.json"), new GsonBuilder().setPrettyPrinting().create().toJson(report) + "\n");
        println("Bounded analysis report written to " + output);
    }
}
