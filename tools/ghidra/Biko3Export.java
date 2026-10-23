// @category Biko3
// Static export only. Decompiled C is not original source or an iOS port.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import com.google.gson.GsonBuilder;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.Instant;
import java.util.*;
import java.util.regex.Pattern;

public class Biko3Export extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) throw new IllegalArgumentException("Expected output directory [maxDecompile=40]");
        Path output = Path.of(args[0]).toAbsolutePath();
        int maximum = args.length > 1 ? Integer.parseInt(args[1]) : 40;
        if (maximum < 1 || maximum > 1000) throw new IllegalArgumentException("Invalid decompilation limit");
        Files.createDirectories(output.resolve("decompiled"));
        Pattern anchors = Pattern.compile("biko3|libnx audout|Renderer ready|explicit Vulkan|Save data|sdmc:|save:/|romfs:|Flow load target|Original menu scene", Pattern.CASE_INSENSITIVE);
        List<Map<String, Object>> strings = new ArrayList<>();
        Set<Function> selected = new LinkedHashSet<>();
        DataIterator dataIterator = currentProgram.getListing().getDefinedData(true);
        while (dataIterator.hasNext() && !monitor.isCancelled()) {
            Data data = dataIterator.next();
            if (!data.hasStringValue() || !(data.getValue() instanceof String)) continue;
            String value = (String) data.getValue();
            if (!anchors.matcher(value).find()) continue;
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("address", "0x" + data.getAddress().toString());
            item.put("value", value);
            List<Map<String, String>> refs = new ArrayList<>();
            ReferenceIterator references = currentProgram.getReferenceManager().getReferencesTo(data.getAddress());
            while (references.hasNext()) {
                Reference ref = references.next();
                Function owner = currentProgram.getFunctionManager().getFunctionContaining(ref.getFromAddress());
                Map<String, String> edge = new LinkedHashMap<>();
                edge.put("from", "0x" + ref.getFromAddress().toString());
                edge.put("type", ref.getReferenceType().toString());
                if (owner != null) {
                    edge.put("function", "0x" + owner.getEntryPoint().toString());
                    selected.add(owner);
                }
                refs.add(edge);
            }
            item.put("references", refs);
            strings.add(item);
        }
        List<Map<String, Object>> functions = new ArrayList<>();
        FunctionIterator iterator = currentProgram.getFunctionManager().getFunctions(true);
        while (iterator.hasNext() && !monitor.isCancelled()) {
            Function function = iterator.next();
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("entry", "0x" + function.getEntryPoint().toString());
            item.put("name", function.getName(true));
            item.put("signature", function.getSignature().toString());
            item.put("body_bytes", function.getBody().getNumAddresses());
            item.put("thunk", function.isThunk());
            List<String> callees = new ArrayList<>();
            for (Function called : function.getCalledFunctions(monitor)) callees.add("0x" + called.getEntryPoint().toString());
            item.put("callees", callees);
            functions.add(item);
        }
        List<Map<String, Object>> decompilation = new ArrayList<>();
        DecompInterface decompiler = new DecompInterface();
        try {
            if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException(decompiler.getLastMessage());
            int count = 0;
            for (Function function : selected) {
                if (monitor.isCancelled() || count++ >= maximum) break;
                println("Decompiling " + function.getEntryPoint() + " " + function.getName());
                DecompileResults result = decompiler.decompileFunction(function, 30, monitor);
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("entry", "0x" + function.getEntryPoint().toString());
                item.put("completed", result.decompileCompleted());
                item.put("diagnostic", result.getErrorMessage());
                if (result.decompileCompleted() && result.getDecompiledFunction() != null) {
                    String filename = "decompiled/" + function.getEntryPoint().toString() + ".c";
                    Files.writeString(output.resolve(filename), "/* Ghidra pseudocode; not original source or asserted compilable. */\n" + result.getDecompiledFunction().getC(), StandardCharsets.UTF_8);
                    item.put("file", filename);
                }
                decompilation.add(item);
            }
        } finally {
            decompiler.dispose();
        }
        Map<String, Object> report = new LinkedHashMap<>();
        report.put("schema", 1);
        report.put("observed_at", Instant.now().toString());
        report.put("authority", "standalone-ghidra-static-analysis");
        report.put("executable_sha256", currentProgram.getExecutableSHA256());
        report.put("image_base", "0x" + Long.toUnsignedString(currentProgram.getImageBase().getOffset(), 16));
        report.put("language", currentProgram.getLanguageID().toString());
        report.put("cancelled", monitor.isCancelled());
        report.put("functions", functions);
        report.put("strings", strings);
        report.put("decompilation", decompilation);
        report.put("selected_anchor_functions", selected.size());
        report.put("limitations", List.of(
            "Static analysis only; no guest execution or iOS validation.",
            "Auto-analysis may miss functions, strings and indirect calls.",
            "Only functions referencing selected defined string anchors are queued; decompilation is capped.",
            "Per-function decompilation timeout is 30 seconds; inspect each completed flag.",
            "Pseudo-C is decompiler output, not original source or an iOS implementation.",
            "This is a standalone Ghidra export, not a REA MCP result."));
        Files.writeString(output.resolve("functions.json"), new GsonBuilder().setPrettyPrinting().create().toJson(report) + "\n", StandardCharsets.UTF_8);
        println("Biko3 export written to " + output);
    }
}
