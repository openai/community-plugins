/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import org.openpnp.model.BoardLocation;
import org.openpnp.model.Job;
import org.openpnp.model.Part;
import org.openpnp.model.Placement;
import org.openpnp.scripting.Scripting;
import org.openpnp.spi.Feeder;
import org.openpnp.spi.Nozzle;
import org.openpnp.spi.NozzleTip;
import org.openpnp.spi.PartAlignment;

/**
 * Durable observations at the pinned native scripting call sites, without executing scripts.
 * Requires native-action-observer.patch; no reflection or access to processor private fields.
 * Construct and use on the native executor. Only snapshot() is intended for another thread.
 *
 * The Sink must append AND force the containing journal before returning, and publish its
 * event only after that force. Sink failure is sticky. A DurabilityFence is deliberately an
 * Error: ReferencePnpJobProcessor.Pick.feed (pinned lines 1421-1438) and feederPickRetry
 * (1472-1485) catch Exception and retry the physical action. The Bridge must catch this exact
 * Error outside next(), fence the operation outcome_unknown, and settle without abort(),
 * discard, cleanup, or replay. It must not turn this fence into a native retryable exception.
 * UnresolvedActionFence separately stops ordinary native retries and cleanup when an earlier
 * Before has no After. If try-with-resources suppresses a close-time fence on an ordinary
 * exception, the Bridge must still classify the sticky snapshot fault as outcome_unknown.
 *
 * A returned hook is evidence that the corresponding native call reached that hook. It is
 * never independent confirmation of a physical effect. Vision.After runs from finally and
 * therefore only reports a result when offsets are present. No placement is marked verified.
 * Source boundaries: ReferencePnpJobProcessor 1344-1358 (placement identity), 1421-1423
 * (feed), 1847-1883/1890-1911 (assembly/complete); ReferenceNozzle 364/385 (pick),
 * 408/435 (release), 658/705 and 768/817 (automatic tip changer); VisionUtils 272/288
 * (alignment, After in finally); Cycles 82/96 (discard). Motion, sensing, postPick,
 * manual tip changes and internal retry counters are not instrumented by those hooks.
 */
public final class NativeActionLedger {
    public interface Sink { void append(String type, Map<String,Object> payload) throws Exception; }
    /** Optional private simulator material context; failures must not enter native Exception retry loops. */
    public interface MaterialObserver {
        Map<String,Object> beforeFeed(Feeder feeder)throws Exception;
        Map<String,Object> afterFeed(Feeder feeder,Map<String,Object> material)throws Exception;
    }
    private MaterialObserver materialObserver;
    public NativeActionLedger withMaterialObserver(MaterialObserver observer){if(current!=null||eventOrdinal!=0)throw new IllegalStateException("Ledger already used");materialObserver=Objects.requireNonNull(observer);return this;}
    public static final class DurabilityFence extends Error {
        private static final long serialVersionUID = 1L;
        private DurabilityFence(Throwable cause) { super("Native action journal durability is unknown", cause); }
    }
    /** A prior action may have taken effect; native retry or cleanup is not authorized. */
    public static final class UnresolvedActionFence extends Error {
        private static final long serialVersionUID = 1L;
        public final String actionId;
        private UnresolvedActionFence(String actionId) {
            super("Native action has no matching after-hook; reconcile before further effects");
            this.actionId = actionId;
        }
    }

    public static final int MAX_PLACEMENTS = 10000;
    public static final int MAX_PENDING = 256;
    public static final int MAX_EVENTS = 250000;
    public static final String PINNED_UPSTREAM = "5bd404cfc70f34103a3ca0fbb6b50c2b465f407c";
    private static final Gson GSON = new Gson();
    private static final Map<String,String> BEFORE = Map.of(
        "Feeder.BeforeFeed", "feed", "Nozzle.BeforePick", "pick", "Nozzle.BeforePlace", "release",
        "Vision.PartAlignment.Before", "align", "Job.BeforeDiscard", "discard",
        "NozzleTip.BeforeLoad", "nozzle_tip_load", "NozzleTip.BeforeUnload", "nozzle_tip_unload");
    private static final Map<String,String> AFTER = Map.of(
        "Feeder.AfterFeed", "feed", "Nozzle.AfterPick", "pick", "Nozzle.AfterPlace", "release",
        "Vision.PartAlignment.After", "align", "Job.AfterDiscard", "discard",
        "NozzleTip.Loaded", "nozzle_tip_load", "NozzleTip.Unloaded", "nozzle_tip_unload");
    private static final Set<String> EVENT_TYPES = Set.of("native_action_intent", "native_action_outcome",
        "native_placement_checkpoint", "native_action_gap");

    private final Sink sink;
    private final Job job;
    private final Map<String,Object> run;
    private final Map<String,Map<String,Object>> loadBindings=new HashMap<>();
    private final IdentityHashMap<BoardLocation,Map<Placement,String>> membership = new IdentityHashMap<>();
    private final IdentityHashMap<Nozzle,Map<String,Object>> contexts = new IdentityHashMap<>();
    private final Map<String,Integer> attempts = new HashMap<>();
    private final Map<String,Integer> placementAttempts = new HashMap<>();
    private final Map<String,Map<String,Object>> pending = new LinkedHashMap<>();
    private final Set<String> placed = new HashSet<>();
    private final Map<String,Integer> counts = new TreeMap<>();
    private volatile Map<String,Object> snapshot;
    private DurabilityFence fence;
    private UnresolvedActionFence unresolvedFence;
    private StepScope current;
    private long lastStep = -1;
    private long eventOrdinal;
    private long actionOrdinal;
    private boolean abortObserved;
    private Map<String,Object> lastCheckpoint = Map.of();

    public NativeActionLedger(Sink sink, String operationId, String jobId, String configRevision,
                              String logicalBoardLoadId, Job job) {
        this(sink,operationId,jobId,configRevision,logicalBoardLoadId,job,null);
    }
    public NativeActionLedger(Sink sink,String operationId,String jobId,String configRevision,
                              String boardLoadScopeId,Job job,Map<String,Object> nativeLoadScope) {
        this.sink = Objects.requireNonNull(sink);
        this.job = Objects.requireNonNull(job);
        Map<String,Object> binding=map("schema_version",1,"operation_id",id(operationId),"job_id",id(jobId),
            "config_revision",id(configRevision),"board_load_id",id(boardLoadScopeId),
            "board_load_authority",nativeLoadScope==null?"logical-run-scope":"native-simulator","physical_load_verified",false);
        if(nativeLoadScope!=null){
            binding.put("board_load_revision",nativeLoadScope.get("board_load_revision"));binding.put("job_revision",nativeLoadScope.get("job_revision"));
            for(Object raw:(List<?>)nativeLoadScope.get("boards")){Map<String,Object> board=(Map<String,Object>)raw;String nativeId=(String)board.get("board_instance_id");if(loadBindings.put(nativeId,immutable(board))!=null)throw new IllegalArgumentException("Duplicate native load binding");}
        }
        run=immutable(binding);
        Set<String> identities = new HashSet<>();
        int total = 0;
        for (BoardLocation board : job.getBoardLocations()) {
            Map<Placement,String> placements = new IdentityHashMap<>();
            for (Placement placement : board.getBoard().getPlacements()) {
                if (++total > MAX_PLACEMENTS) throw new IllegalArgumentException("Native ledger placement limit");
                Map<String,Object> load=loadBindings.get(board.getUniqueId());
                if(nativeLoadScope!=null&&load==null)throw new IllegalArgumentException("Native board has no simulator load binding");
                String identity = load==null?GSON.toJson(List.of(id(board.getUniqueId()), id(placement.getId()))):GSON.toJson(List.of(run.get("job_revision"),load.get("loaded_board_id"),id(placement.getId())));
                if (!identities.add(identity)) throw new IllegalArgumentException("Ambiguous native placement identity");
                placements.put(placement, identity);
            }
            membership.put(board, placements);
        }
        refreshSnapshot();
    }

    /** Each scope must enclose exactly one actual processor.next() on the native thread. */
    public StepScope openStep(long nativeStepIndex) {
        if (fence != null) throw fence;
        if (unresolvedFence != null) throw unresolvedFence;
        if (current != null || abortObserved || nativeStepIndex <= lastStep || nativeStepIndex < 0)
            throw new IllegalStateException("Native ledger step order or ownership conflict");
        if (eventOrdinal >= MAX_EVENTS - MAX_PENDING - 64)
            throw new IllegalStateException("Native action ledger capacity requires a new run");
        StepScope scope = new StepScope(nativeStepIndex, "processor-next");
        current = scope;
        lastStep = nativeStepIndex;
        return scope;
    }

    /** Ordinary, separately authorized abort only. Never call after either sticky fence. */
    public StepScope openAbortScope() {
        if (fence != null) throw fence;
        if (unresolvedFence != null) throw unresolvedFence;
        if (current != null || abortObserved) throw new IllegalStateException("Native abort scope order or ownership conflict");
        StepScope scope = new StepScope(lastStep, "processor-abort");
        current = scope;
        abortObserved = true;
        return scope;
    }

    public final class StepScope implements AutoCloseable {
        private final long index;
        private final String nativeCall;
        private final Thread owner = Thread.currentThread();
        private final Scripting.NativeObserverScope registration;
        private boolean complete;
        private boolean closed;
        private StepScope(long index, String nativeCall) {
            this.index = index;
            this.nativeCall = nativeCall;
            registration = Scripting.observeNativeEvents(new Scripting.NativeObserver() {
                @Override public void beforeScripts(String event, Map<String,Object> globals) {
                    observe(event, globals, false);
                }
                @Override public void afterScripts(String event, Map<String,Object> globals) {
                    observe(event, globals, true);
                }
            });
        }
        public void complete() {
            assertOwner();
            if (closed || complete) throw new IllegalStateException("Native ledger step already finished");
            if (fence != null) throw fence;
            fenceUnpaired("native-step-returned-without-matching-after-hook");
            complete = true;
        }
        private void assertOwner() {
            if (Thread.currentThread() != owner) throw new IllegalStateException("Native ledger executor changed");
        }
        @Override public void close() {
            assertOwner();
            if (closed) return;
            try {
                if (!complete && fence == null && unresolvedFence == null) fenceUnpaired("native-step-threw-or-was-interrupted");
            } finally {
                registration.close();
                closed = true;
                current = null;
                refreshSnapshot();
            }
        }
    }

    private void observe(String event, Map<String,Object> globals, boolean afterScripts) {
        if (fence != null) throw fence;
        if (unresolvedFence != null) throw unresolvedFence;
        if (current == null || Thread.currentThread() != current.owner)
            throw new IllegalStateException("Native action hook is outside its step");
        Map<String,Object> args = globals == null ? Map.of() : globals;
        if (!afterScripts && "Job.Error".equals(event)) {
            fenceUnpaired("native-job-error-with-unresolved-action");
            return;
        }
        // Existing before-scripts may tune native model values; persist intent after they return.
        if (afterScripts && "Job.Placement.Starting".equals(event)) {
            Map<String,Object> context = placementContext(args);
            Nozzle nozzle = typed(args, "nozzle", Nozzle.class);
            if (context == null || nozzle == null) { gap(event, "native-placement-context-not-in-bound-job"); return; }
            String key = (String) context.get("placement_key");
            int ordinal = placementAttempts.getOrDefault(key, 0) + 1;
            Map<String,Object> updated = mutable(context);
            updated.put("placement_attempt_observed", ordinal);
            updated.put("attempt_basis", "placement-starting-hook-occurrence");
            checkpoint(event, updated, "placement-starting");
            placementAttempts.put(key, ordinal);
            contexts.put(nozzle, immutable(updated));
        } else if (afterScripts && "Job.Placement.BeforeAssembly".equals(event)) {
            Map<String,Object> context = placementContext(args);
            Nozzle nozzle = typed(args, "nozzle", Nozzle.class);
            if (context == null || nozzle == null) { gap(event, "native-placement-context-not-in-bound-job"); return; }
            Map<String,Object> previous = contexts.get(nozzle);
            Map<String,Object> updated = mutable(context);
            if (previous != null && Objects.equals(previous.get("placement_key"), context.get("placement_key"))) {
                copyIfPresent(previous, updated, "placement_attempt_observed", "attempt_basis", "feeder_id");
            }
            checkpoint(event, updated, "before-assembly");
            contexts.put(nozzle, immutable(updated));
        } else if (!afterScripts && "Job.Placement.Complete".equals(event)) {
            Map<String,Object> context = placementContext(args);
            if (context == null) { gap(event, "native-placement-context-not-in-bound-job"); return; }
            BoardLocation board = typed(args, "boardLocation", BoardLocation.class);
            Placement placement = typed(args, "placement", Placement.class);
            Map<String,Object> observation = mutable(context);
            observation.put("native_placed_status", job.retrievePlacedStatus(board, placement.getId()));
            observation.put("independently_verified", false);
            checkpoint(event, observation, "native-placement-complete-hook");
            if (Boolean.TRUE.equals(observation.get("native_placed_status"))) placed.add((String)context.get("placement_key"));
            Nozzle nozzle = typed(args, "nozzle", Nozzle.class);
            if (nozzle != null) contexts.remove(nozzle);
            refreshSnapshot();
        } else if (afterScripts && BEFORE.containsKey(event)) {
            startAction(BEFORE.get(event), event, args);
        } else if (!afterScripts && AFTER.containsKey(event)) {
            finishAction(AFTER.get(event), event, args);
        }
    }

    private void startAction(String kind, String event, Map<String,Object> args) {
        Nozzle nozzle = typed(args, "nozzle", Nozzle.class);
        Map<String,Object> context = actionContext(nozzle, args);
        if("feed".equals(kind)&&materialObserver!=null){try{
            Feeder feeder=typed(args,"feeder",Feeder.class);if(feeder==null)throw new IllegalArgumentException("Feed hook missing native feeder");
            Map<String,Object> material=materialObserver.beforeFeed(feeder);if(!material.isEmpty()){context=mutable(context);context.put("material_load",immutable(material));context=immutable(context);}
        }catch(Exception failure){throwDurability(failure);}}
        String slot = kind + ":" + context.get("nozzle_id");
        if (pending.containsKey(slot)) fenceUnpaired("another-native-before-hook-without-matching-after-hook");
        // Native failure handlers may discard before returning from next(). Do not let cleanup
        // obscure an unresolved feed or pick. The known nested discard -> nozzle release pair
        // is the sole allowed overlap between existing native action hooks.
        boolean nestedDiscardRelease = "release".equals(kind) && pending.size() == 1 &&
            pending.containsKey("discard:" + context.get("nozzle_id"));
        if (!pending.isEmpty() && !nestedDiscardRelease)
            fenceUnpaired("another-native-action-with-unresolved-prior-action");
        if (pending.size() >= MAX_PENDING) {
            // Capacity is a local journal admission failure, never a native action retry.
            throwDurability(new java.io.IOException("Native action intent capacity exhausted"));
        }
        String counterKey = kind + ":" + context.getOrDefault("placement_key", "unbound") + ":" + context.get("nozzle_id");
        int attempt = attempts.getOrDefault(counterKey, 0) + 1;
        Map<String,Object> record = base();
        record.putAll(map("action_id", run.get("operation_id") + "/native-action-" + (actionOrdinal + 1),
            "kind", kind, "hook", event, "context", context, "action_attempt_observed", attempt,
            "attempt_basis", "before-hook-occurrence", "state", "intent", "physical_outcome", "unknown"));
        emit("native_action_intent", record);
        actionOrdinal++;
        attempts.put(counterKey, attempt);
        pending.put(slot, immutable(record));
        refreshSnapshot();
    }

    private void finishAction(String kind, String event, Map<String,Object> args) {
        Nozzle nozzle = typed(args, "nozzle", Nozzle.class);
        String slot = kind + ":" + nativeId(nozzle == null ? null : nozzle.getId());
        Map<String,Object> intent = pending.get(slot);
        if (intent == null) { gap(event, "after-hook-without-matching-intent"); return; }
        @SuppressWarnings("unchecked") Map<String,Object> context = (Map<String,Object>) intent.get("context");
        Part part = typed(args, "part", Part.class);
        Feeder feeder = typed(args, "feeder", Feeder.class);
        NozzleTip tip = typed(args, "nozzleTip", NozzleTip.class);
        if ((part != null && !Objects.equals(nativeId(part.getId()), context.get("part_id"))) ||
            (feeder != null && !Objects.equals(nativeId(feeder.getId()), context.get("feeder_id"))) ||
            (tip != null && !Objects.equals(nativeId(tip.getId()), context.get("nozzle_tip_id")))) {
            fenceUnpaired("after-hook-context-mismatch");
            return;
        }
        if("feed".equals(kind)&&context.get("material_load") instanceof Map){
            try{
                Map<String,Object> after=materialObserver.afterFeed(feeder,(Map<String,Object>)context.get("material_load"));
                Map<String,Object> augmented=mutable(intent);augmented.put("material_after",after);
                outcome(slot,augmented,Boolean.TRUE.equals(after.get("expected_delta_observed"))?"native_hook_returned":"outcome_unknown",event,"native-index-observation-only");return;
            }catch(Exception failure){throwDurability(failure);}
        }
        if ("align".equals(kind) && !(args.get("offsets") instanceof PartAlignment.PartAlignmentOffset)) {
            // VisionUtils.findPartAlignmentOffsets emits After in finally, including failures.
            outcome(slot, intent, "outcome_unknown", event, "alignment-finally-hook-without-result");
        } else {
            outcome(slot, intent, "native_hook_returned", event, "physical-effect-not-independently-observed");
        }
    }

    private Map<String,Object> placementContext(Map<String,Object> args) {
        if (args.get("job") != job) return null;
        BoardLocation board = typed(args, "boardLocation", BoardLocation.class);
        Placement placement = typed(args, "placement", Placement.class);
        Map<Placement,String> members = membership.get(board);
        if (members == null || !members.containsKey(placement)) return null;
        Nozzle nozzle = typed(args, "nozzle", Nozzle.class);
        Part part = placement.getPart();
        Map<String,Object> context = map("placement_key", members.get(placement),
            "board_instance_id", nativeId(board.getUniqueId()), "placement_id", nativeId(placement.getId()),
            "part_id", nativeId(part == null ? null : part.getId()), "placement_context", "native-job-hook");
        Map<String,Object> load=loadBindings.get(board.getUniqueId());
        if(load!=null){context.put("board_load_id",load.get("board_load_id"));context.put("loaded_board_id",load.get("loaded_board_id"));context.put("loaded_side",load.get("side"));context.put("root_instance_id",load.get("root_instance_id"));}
        if (nozzle != null) {
            context.put("nozzle_id", nativeId(nozzle.getId()));
            context.put("nozzle_tip_id", nativeId(nozzle.getNozzleTip() == null ? null : nozzle.getNozzleTip().getId()));
        }
        Feeder feeder = typed(args, "feeder", Feeder.class);
        if (feeder != null) context.put("feeder_id", nativeId(feeder.getId()));
        return context;
    }

    private Map<String,Object> actionContext(Nozzle nozzle, Map<String,Object> args) {
        Map<String,Object> previous = nozzle == null ? null : contexts.get(nozzle);
        Map<String,Object> context = previous == null ? map("placement_context", "unavailable") : mutable(previous);
        context.put("nozzle_id", nativeId(nozzle == null ? null : nozzle.getId()));
        Part part = typed(args, "part", Part.class);
        if (part == null && nozzle != null) part = nozzle.getPart();
        // Tip/discard/preflight hooks may be unrelated to a pending placement. Never assign a
        // stale placement to a different part merely because the same nozzle is involved.
        if (previous != null && part != null && !Objects.equals(previous.get("part_id"), nativeId(part.getId()))) {
            context = map("nozzle_id", context.get("nozzle_id"), "placement_context", "unavailable-part-mismatch");
        }
        if (part != null) context.put("part_id", nativeId(part.getId()));
        Feeder feeder = typed(args, "feeder", Feeder.class);
        if (feeder == null && nozzle != null) feeder = nozzle.getPartsFeeder();
        if (feeder != null) context.put("feeder_id", nativeId(feeder.getId()));
        NozzleTip tip = typed(args, "nozzleTip", NozzleTip.class);
        if (tip == null && nozzle != null) tip = nozzle.getNozzleTip();
        if (tip != null) {
            context.put("nozzle_tip_id", nativeId(tip.getId()));
            context.put("nozzle_tip_context", args.get("nozzleTip") instanceof NozzleTip ? "native-hook-target" : "observed-nozzle-model");
        }
        context.put("native_retry_policy", "fence-if-prior-native-hook-outcome-is-unresolved");
        context.put("internal_retry_index", "not-exposed-by-hook");
        return immutable(context);
    }

    private void outcome(String slot, Map<String,Object> intent, String state, String hook, String reason) {
        Map<String,Object> record = mutable(intent);
        record.remove("event_id");
        record.remove("ledger_sequence");
        record.putAll(map("state", state, "after_hook", hook, "reason", reason,
            "physical_outcome", "unknown", "native_step_index", current.index));
        emit("native_action_outcome", record);
        // A finally hook without a result is not a completed action. Keep its explicit
        // outcome visible and fence before another native action or scope completion.
        if("outcome_unknown".equals(state))pending.put(slot,immutable(record));else pending.remove(slot);
        counts.merge(intent.get("kind") + ":" + state, 1, Integer::sum);
        refreshSnapshot();
    }

    private void fenceUnpaired(String reason) {
        if (pending.isEmpty()) return;
        Map<String,Object> intent = pending.values().iterator().next();
        Map<String,Object> record = base();
        String actionId = (String)intent.get("action_id");
        record.putAll(map("reason", reason, "unresolved_action_id", actionId,
            "state", "outcome_unknown", "physical_outcome", "unknown", "retry_blocked", true));
        emit("native_action_gap", record);
        unresolvedFence = new UnresolvedActionFence(actionId);
        refreshSnapshot();
        throw unresolvedFence;
    }

    private void checkpoint(String hook, Map<String,Object> context, String state) {
        Map<String,Object> record = base();
        record.putAll(map("hook", hook, "context", context, "state", state, "independently_verified", false));
        emit("native_placement_checkpoint", record);
        lastCheckpoint = immutable(record);
        refreshSnapshot();
    }

    private void gap(String hook, String reason) {
        Map<String,Object> record = base();
        String actionId = run.get("operation_id") + "/unmatched-hook-" + (eventOrdinal + 1);
        record.putAll(map("hook", hook, "reason", reason, "physical_outcome", "unknown",
            "state", "outcome_unknown", "retry_blocked", true, "unresolved_action_id", actionId));
        emit("native_action_gap", record);
        counts.merge("context_gaps", 1, Integer::sum);
        unresolvedFence = new UnresolvedActionFence(actionId);
        refreshSnapshot();
        throw unresolvedFence;
    }

    private Map<String,Object> base() {
        Map<String,Object> result = mutable(run);
        result.put("native_step_index", current.index);
        result.put("native_call", current.nativeCall);
        return result;
    }

    private void emit(String type, Map<String,Object> record) {
        if (fence != null) throw fence;
        record.put("ledger_sequence", eventOrdinal + 1);
        record.put("event_id", run.get("operation_id") + "/native-ledger-" + (eventOrdinal + 1));
        try { sink.append(type, immutable(record)); }
        catch (Exception error) { throwDurability(error); }
        eventOrdinal++;
    }

    private void throwDurability(Throwable cause) {
        fence = new DurabilityFence(cause);
        refreshSnapshot();
        throw fence;
    }

    private void refreshSnapshot() {
        snapshot = immutable(map("scope", run, "events_committed", eventOrdinal,
            "actions_started", actionOrdinal, "pending_actions", new ArrayList<>(pending.values()),
            "outcomes", new TreeMap<>(counts), "native_placed_observed", placed.size(),
            "independently_verified", 0, "durability_fault", fence != null,
            "unresolved_action_fault", unresolvedFence != null,
            "last_checkpoint", lastCheckpoint, "coverage", coverage()));
    }

    public Map<String,Object> snapshot() { return snapshot; }

    public static Map<String,Object> coverage() {
        return immutable(map("source", "pinned-native-script-call-sites-with-java-observer", "upstream_commit", PINNED_UPSTREAM,
            "true_action_call_boundaries", false, "physical_effect_verification", false, "automatic_replay", false,
            "observed_pairs", List.of("feed", "pick", "release", "align", "discard", "automatic-nozzle-tip-load", "automatic-nozzle-tip-unload"),
            "gaps", List.of("motion-between-hooks", "part-on-off-sensing-verdicts", "feeder-post-pick", "physical-board-load-and-material-lot",
                "native-internal-retry-index", "actions-within-user-scripts", "manual-nozzle-tip-change-paths", "alignment-skipped-with-no-aligner",
                "nozzle-tip-model-and-calibration-changes-after-changer-hook",
                "native-exception-between-before-and-after-hooks", "process-death-after-effect-before-durable-after-hook")));
    }

    /**
     * Read-only replay of one operation's parsed journal records. Valid duplicate records are
     * idempotent; conflicting duplicates, missing prefixes, gaps, and unmatched outcomes reject.
     * The caller must scan an integrity-checked complete journal, not the bounded event buffer.
     * Returns unresolved intents as unknown; it has no machine reference and cannot replay effects.
     */
    public static Map<String,Object> recover(Iterable<Map<String,Object>> records, String operationId) {
        Replay replay = new Replay(operationId);
        for (Map<String,Object> record : records) replay.accept(record);
        return replay.snapshot();
    }

    public static boolean isLedgerEventType(String type) { return EVENT_TYPES.contains(type); }

    /** Feed during one complete journal scan, then retain only snapshot() and discard this
     * accumulator. It stores receipt digests and unresolved facts, never full journal payloads.
     * A rejected event poisons the accumulator: callers cannot ignore corruption and continue. */
    public static final class Replay {
        private final String operationId;
        private boolean rejected;
        private final Map<String,String> receipts = new HashMap<>();
        private final Map<String,Map<String,Object>> open = new LinkedHashMap<>();
        private final Set<String> actionIds = new HashSet<>();
        private final List<Map<String,Object>> safetyGaps = new ArrayList<>();
        private final Map<String,Map<String,Object>> explicitUnknown = new LinkedHashMap<>();
        private long expected = 1;
        private int outcomes = 0;
        private Map<String,Object> observedRun = null;
        public Replay(String operationId) { this.operationId = id(operationId); }
        public void accept(Map<String,Object> envelope) {
            if (rejected) throw new IllegalStateException("Native ledger replay is invalid");
            try { acceptOne(envelope); }
            catch (RuntimeException error) { rejected = true; throw error; }
        }
        private void acceptOne(Map<String,Object> envelope) {
            String type = String.valueOf(envelope.get("type"));
            if (!EVENT_TYPES.contains(type)) return;
            Object raw = envelope.get("payload");
            if (!(raw instanceof Map)) throw new IllegalArgumentException("Native ledger payload missing");
            @SuppressWarnings("unchecked") Map<String,Object> payload = (Map<String,Object>) raw;
            if (!operationId.equals(payload.get("operation_id"))) return;
            if (receipts.size() >= MAX_EVENTS) throw new IllegalArgumentException("Native ledger replay limit");
            String eventId = id(String.valueOf(payload.get("event_id")));
            String digest = sha256(immutable(map("type", type, "payload", payload)));
            if (receipts.containsKey(eventId)) {
                if (!receipts.get(eventId).equals(digest)) throw new IllegalArgumentException("Native ledger receipt collision");
                return;
            }
            Object seq = payload.get("ledger_sequence");
            if (!(seq instanceof Number) || NativeJournalJson.integer(seq,1,MAX_EVENTS) != expected ||
                !eventId.equals(operationId + "/native-ledger-" + expected))
                throw new IllegalArgumentException("Native ledger missing prefix or sequence gap");
            Map<String,Object> scope = map("job_id", payload.get("job_id"), "config_revision", payload.get("config_revision"),
                "board_load_id", payload.get("board_load_id"), "board_load_authority", payload.get("board_load_authority"));
            if (observedRun != null && !observedRun.equals(scope)) throw new IllegalArgumentException("Native ledger scope changed");
            observedRun = scope;
            receipts.put(eventId, digest);
            expected++;
            if ("native_action_intent".equals(type)) {
                String actionId = id(String.valueOf(payload.get("action_id")));
                if (!actionIds.add(actionId) || open.size() >= MAX_PENDING)
                    throw new IllegalArgumentException("Native action identity collision or pending limit");
                open.put(actionId, immutable(payload));
            } else if ("native_action_outcome".equals(type)) {
                String actionId = id(String.valueOf(payload.get("action_id")));
                Map<String,Object> intent = open.remove(actionId);
                if (intent == null || !Objects.equals(intent.get("kind"), payload.get("kind")) ||
                    !Objects.equals(intent.get("context"), payload.get("context")))
                    throw new IllegalArgumentException("Native outcome has no matching context-bound intent");
                if("outcome_unknown".equals(payload.get("state"))){
                    if(explicitUnknown.size()>=MAX_PENDING)throw new IllegalArgumentException("Native explicit unknown outcome limit");
                    explicitUnknown.put(actionId,immutable(payload));
                }else if(!"native_hook_returned".equals(payload.get("state")))throw new IllegalArgumentException("Unknown native action outcome state");
                outcomes++;
            } else if ("native_action_gap".equals(type) && "outcome_unknown".equals(payload.get("state"))) {
                if (safetyGaps.size() >= MAX_PENDING) throw new IllegalArgumentException("Native ledger safety gap limit");
                safetyGaps.add(immutable(payload));
            }
        }
        public Map<String,Object> snapshot() {
            if (rejected) throw new IllegalStateException("Native ledger replay is invalid");
        List<Map<String,Object>> unknown = new ArrayList<>();
        for (Map<String,Object> intent : open.values()) {
            Map<String,Object> value = mutable(intent);
            value.put("state", "outcome_unknown");
            value.put("reason", "journal-ended-before-matching-durable-after-hook");
            unknown.add(value);
        }
        for(Map<String,Object> reported:explicitUnknown.values())unknown.add(mutable(reported));
        return immutable(map("operation_id", operationId, "events_committed", receipts.size(),
            "native_hook_outcomes", outcomes, "unresolved_actions", unknown, "automatic_replay", false,
            "safety_gaps", safetyGaps, "requires_reconciliation", !unknown.isEmpty() || !safetyGaps.isEmpty(),
            "physical_effect_verification", false));
        }

    }

    private static <T> T typed(Map<String,Object> args, String key, Class<T> type) {
        return type.isInstance(args.get(key)) ? type.cast(args.get(key)) : null;
    }
    private static String id(String value) {
        if (value == null || value.isBlank() || value.length() > 512 || value.chars().anyMatch(c -> c < 32 || c == 127))
            throw new IllegalArgumentException("Invalid native ledger identity");
        return value;
    }
    private static String nativeId(String value) { return value == null ? "unavailable" : id(value); }
    private static void copyIfPresent(Map<String,Object> from, Map<String,Object> to, String... keys) {
        for (String key : keys) if (from.containsKey(key)) to.put(key, from.get(key));
    }
    private static Map<String,Object> map(Object... pairs) {
        Map<String,Object> result = new LinkedHashMap<>();
        for (int i = 0; i < pairs.length; i += 2) result.put((String)pairs[i], pairs[i+1]);
        return result;
    }
    private static Map<String,Object> mutable(Map<String,Object> value) { return new LinkedHashMap<>(value); }
    @SuppressWarnings("unchecked") private static <T> T freeze(Object value) {
        if (value instanceof Map) {
            Map<String,Object> out = new TreeMap<>();
            ((Map<?,?>)value).forEach((key, item) -> out.put((String)key, freeze(item)));
            return (T)Collections.unmodifiableMap(out);
        }
        if (value instanceof Collection) {
            List<Object> out = new ArrayList<>();
            for (Object item : (Collection<?>)value) out.add(freeze(item));
            return (T)Collections.unmodifiableList(out);
        }
        if (value == null || value instanceof String || value instanceof Number || value instanceof Boolean) return (T)value;
        throw new IllegalArgumentException("Non-scalar native ledger data");
    }
    private static Map<String,Object> immutable(Map<String,Object> value) { return freeze(value); }
    private static String sha256(Map<String,Object> value) {
        try {
            byte[] bytes = MessageDigest.getInstance("SHA-256").digest(GSON.toJson(value).getBytes(StandardCharsets.UTF_8));
            StringBuilder encoded = new StringBuilder(bytes.length * 2);
            for (byte item : bytes) encoded.append(Character.forDigit((item & 255) >>> 4, 16)).append(Character.forDigit(item & 15, 16));
            return encoded.toString();
        } catch (java.security.NoSuchAlgorithmException impossible) { throw new AssertionError(impossible); }
    }
}
