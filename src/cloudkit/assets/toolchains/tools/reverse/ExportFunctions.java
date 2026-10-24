// Export a requested function's actual Ghidra decompilation as structured evidence.
// @category CodexVerification
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.nio.file.Files;
import java.nio.file.Path;

public class ExportFunctions extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] arguments = getScriptArgs();
        if (arguments.length != 2) throw new IllegalArgumentException("Need output JSON and exact function name");
        DecompInterface decompiler = new DecompInterface();
        JsonArray functions = new JsonArray();
        if (!decompiler.openProgram(currentProgram)) throw new IllegalStateException("Cannot open program");
        try {
            for (Function function : currentProgram.getFunctionManager().getFunctions(true)) {
                if (!function.getName().equals(arguments[1])) continue;
                DecompileResults result = decompiler.decompileFunction(function, 60, monitor);
                if (!result.decompileCompleted() || result.getDecompiledFunction() == null)
                    throw new IllegalStateException(result.getErrorMessage());
                JsonObject item = new JsonObject();
                item.addProperty("name", function.getName());
                item.addProperty("address", function.getEntryPoint().toString());
                item.addProperty("c", result.getDecompiledFunction().getC());
                functions.add(item);
            }
        } finally { decompiler.dispose(); }
        if (functions.isEmpty()) throw new IllegalArgumentException("Requested function was not found");
        JsonObject report = new JsonObject();
        report.addProperty("program", currentProgram.getName());
        report.addProperty("language", currentProgram.getLanguageID().toString());
        report.addProperty("static_analysis_only", true);
        report.add("functions", functions);
        Files.writeString(Path.of(arguments[0]), report.toString() + "\n");
        println("CODEX_DECOMPILATION_EXPORTED=" + functions.size());
    }
}
