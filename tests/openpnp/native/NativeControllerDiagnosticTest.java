/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.nio.file.Paths;
import java.util.Set;
import org.openpnp.codex.prototype.tagged.NativeDiagnosticBridgeTest;
import org.openpnp.codex.prototype.tagged.NativeDiagnosticFaultTest;

/** Actual controller admission, native execution and publication cases in isolated child JVMs. */
public final class NativeControllerDiagnosticTest {
    private static final Set<String> BRIDGE_CASES=Set.of("normal","arguments","old-ack","expired-dequeue","wrapper-error","configuration-save-failure");
    public static void main(String[] args)throws Exception{
        if(args.length==1){
            NativeCompletionCases.run(NativeControllerDiagnosticTest.class,Paths.get(args[0]),"BRIDGE_DIAGNOSTIC_RESULT ",
                "normal","arguments","old-ack","expired-dequeue","admission-force","binding-force","intent-before-write","intent-force","outcome-force","retirement-force","capacity","model-pre-force","model-post-force","lease-after-connect","close-observed-fault","wrapper-error","configuration-save-failure");
            return;
        }
        if(args.length!=3)throw new IllegalArgumentException("Expected samples, or internal case/fixture/samples arguments");
        if(BRIDGE_CASES.contains(args[0]))NativeDiagnosticBridgeTest.main(args);else NativeDiagnosticFaultTest.main(args);
    }
}
