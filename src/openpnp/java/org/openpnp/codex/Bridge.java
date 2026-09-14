/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.LinkOption;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;
import javax.imageio.ImageIO;
import org.openpnp.Main;
import org.openpnp.gui.MainFrame;
import org.openpnp.machine.reference.ReferenceMachine;
import org.openpnp.machine.reference.ReferenceNozzle;
import org.openpnp.machine.reference.ReferencePnpJobProcessor;
import org.openpnp.machine.reference.ReferenceNozzleTip;
import org.openpnp.machine.reference.ReferenceNozzleTipCalibration;
import org.openpnp.machine.reference.camera.ReferenceCamera;
import org.openpnp.machine.reference.feeder.ReferenceTrayFeeder;
import org.openpnp.model.*;
import org.openpnp.spi.*;
import org.openpnp.spi.MotionPlanner.CompletionType;
import org.openpnp.util.MovableUtils;
import org.openpnp.util.Utils2D;

/** Narrow native API. Hardware and GUI ownership are deliberately unqualified in this release. */
public final class Bridge implements AutoCloseable {
    public static final String UPSTREAM = "5bd404cfc70f34103a3ca0fbb6b50c2b465f407c";
    public static final int MAX_REQUEST_BYTES=8*1024*1024;
    public static final int MAX_JOURNAL_RECORD_BYTES=8*1024*1024;
    private static final long MAX_ARTIFACT_STORAGE_BYTES=1024L*1024*1024;
    private static final int MAX_REQUESTS=20000,MAX_PLANS=128,MAX_ARTIFACT_METADATA=128;
    private static final long MAX_JOURNAL_BYTES=512L*1024*1024,PLAN_TTL_NANOS=TimeUnit.MINUTES.toNanos(5);
    private static final Gson GSON = new Gson();
    private static final Gson VACUUM_JSON = new com.google.gson.GsonBuilder().serializeNulls().create();
    private static final Set<String> READS = new HashSet<>(Arrays.asList(
        "openpnp_get_capabilities", "openpnp_get_status", "openpnp_get_configuration",
        "openpnp_get_operation", "openpnp_get_events", "openpnp_get_artifact", "openpnp_get_control_session"));
    private static final Set<String> SIM_CLASSES = new HashSet<>(Arrays.asList(
        "org.openpnp.machine.reference.driver.NullDriver",
        "org.openpnp.machine.reference.camera.ImageCamera",
        "org.openpnp.machine.reference.camera.SimulatedUpCamera",
        "org.openpnp.machine.reference.feeder.ReferenceStripFeeder",
        "org.openpnp.machine.reference.feeder.ReferenceTrayFeeder",
        "org.openpnp.machine.reference.ReferenceActuator",
        "org.openpnp.machine.reference.ReferenceHead", "org.openpnp.machine.reference.ReferenceNozzle"));
    private final Configuration config;
    private final Machine machine;
    private final boolean managedSimulator;
    private final GuiOwnership guiOwnership;
    private final String simulatorProfile;
    private final Path journalDir;
    private final Path sampleRoot;
    private final String token;
    private final String machineId;
    private final String instance = UUID.randomUUID().toString();
    private final String loadedBridgeSha256=loadedBridgeSha256();
    private final long startedNanos=System.nanoTime();
    private HttpServer server;
    private final ExecutorService httpThreads = Executors.newFixedThreadPool(4);
    private final ScheduledExecutorService watchdog = Executors.newSingleThreadScheduledExecutor();
    private JobProcessor.TextStatusListener statusListener;
    private NativeJobDocuments jobDocuments;
    private NativeReplacementDocuments replacementDocuments;
    private final Map<String,String> documentArtifacts=new HashMap<>();
    private FileChannel journal;
    private FileLock journalLock;
    private final LinkedHashMap<String, Map<String,Object>> operations = new LinkedHashMap<>();
    private final Map<String,String> requests = new HashMap<>();
    private final Map<String,String> requestDigests = new HashMap<>();
    private final Map<String,Map<String,Object>> commandReceipts = new HashMap<>();
    private final Map<String,Map<String,Object>> sessionReceipts = new HashMap<>();
    private final LinkedHashMap<String, Map<String,Object>> plans = new LinkedHashMap<>();
    private final Map<String,Long> planDeadlines=new HashMap<>();
    private final Map<String,NativeSettings.Patch> configurationPlans = new HashMap<>();
    private final Map<String,NativePlacementEdits.Patch> placementPlans=new HashMap<>();
    private final LinkedHashMap<String, Map<String,Object>> artifacts = new LinkedHashMap<>();
    private final LinkedHashMap<String,NativeConfigurationSnapshots.Snapshot> typedBackups = new LinkedHashMap<>();
    private final ArrayList<Map<String,Object>> events = new ArrayList<>();
    private volatile Map<String,Object> snapshot = new LinkedHashMap<>();
    private volatile Map<String,Object> jobProgress=map("state","absent");
    private long sequence;
    private long artifactBytes,artifactCount;
    private long revision = 1;
    private long epoch = 1;
    private String session;
    private long sessionDeadline;
    private volatile boolean journalFault;
    private volatile boolean configurationFault;
    private volatile boolean topologyFault;
    private volatile boolean closed;
    private volatile boolean pauseRequested;
    private volatile boolean abortRequested;
    private String activeOperation;
    private final ThreadLocal<String> nativeOperationContext=new ThreadLocal<>();
    /** One actual native Future owns admission through durable completion publication. */
    private final ThreadLocal<NativeSubmission> submissionContext=new ThreadLocal<>();
    private NativeSubmission pendingSubmission;
    private boolean explicitUnknownExit;
    private static final class NativeSubmission {
        final String token=UUID.randomUUID().toString(),operationId;
        final long owner;
        final boolean jobTask,localCleanup;
        Future<?> future;
        boolean entered,exited,publicationAttempted,jobPresent,releaseActive;
        String desiredJobState,phase="awaiting-native-wrapper";
        Map<String,Object> outcome,progress,bodyFailure,publicationFault,completionObservation;
        NativeSubmission(String id,long owner,boolean jobTask,boolean localCleanup){this.operationId=id;this.owner=owner;this.jobTask=jobTask;this.localCleanup=localCleanup;}
    }
    private final NativeVacuumJournal vacuumJournal=new NativeVacuumJournal();
    private final NativeSensingReconciliation sensingReconciliation=new NativeSensingReconciliation(vacuumJournal,this::sensingWrapperCompleted,this::sensingDispositionsVerified,this::restartObservationsVerified);
    private final Map<String,NativeSubmission> sensingCompletedWrappers=new HashMap<>();
    private PendingSensingReconciliation pendingSensingReconciliation;
    private static final class PendingSensingReconciliation {
        final String id,requestOperation;final long owner,deadline;
        final NativeSensingReconciliation.Capture capture;final Map<String,Object> task;
        final List<NativeVacuumSensing.Plan> plans;
        boolean active=true,presented,used,intentWritten,verified,resolved,completionDelivered,unknownRecorded;
        String localOperation;Map<String,Object> dispositions;
        NativeSensingReconciliation.Permit permit;
        NativeFaultedJobReplacement.Capture replacementCapture;
        NativeFaultedJobReplacement.Permit replacementPermit;
        NativeFaultedJobReplacement.ContinuationCapture continuationCapture;
        NativeFaultedJobReplacement.ContinuationPermit continuationPermit;
        NativeFaultedJobReplacement.Publication continuationPublication;
        NativeFaultedJobReplacement.RestartStage restartStage;
        NativeFaultedJobReplacement.RestartCapture restartCapture;
        NativeFaultedJobReplacement.RestartPermit restartPermit;
        GuiSensingFixture.RestartAttestation restartAttestation;
        Map<String,Object> restartObservations;
        boolean restartCompleted;
        Map<String,Object> capturedSource;String originalJobOperation;
        java.util.function.BooleanSupplier localAuthority;
        CompletableFuture<Map<String,Object>> completion;
        final java.util.concurrent.atomic.AtomicBoolean queued=new java.util.concurrent.atomic.AtomicBoolean();
        PendingSensingReconciliation(String id,String request,long owner,NativeSensingReconciliation.Capture capture,Map<String,Object> task,List<NativeVacuumSensing.Plan> plans){
            this.id=id;requestOperation=request;this.owner=owner;this.capture=capture;this.task=task;this.plans=List.copyOf(plans);deadline=System.nanoTime()+TimeUnit.MINUTES.toNanos(5);
        }
    }
    private final Map<String,NativeVacuumSensing.Plan> vacuumPlans=new HashMap<>();
    private NativeVacuumSources.Admission vacuumJobAdmission;
    private Map<String,Object> vacuumSources=Collections.emptyMap();
    private final boolean nativeLedgerAvailable=nativeObserverAvailable();
    private final Map<String,Object> cameraScaleRuntime=cameraScaleRuntimeCapabilities();
    private NativeActionLedger nativeLedger;
    private NativeBoardLoads boardLoads;
    private NativeMaterialLoads materialLoads;
    private String validatedMaterialRevision;
    private NativeJobLineage jobLineage;
    private NativeFaultedJobReplacement faultedJobReplacement;
    private final Map<String,StructurePlan> structurePlans=new HashMap<>();
    private final NativePortableLaunch portableLaunch;
    private String validatedBoardLoadRevision;
    private String nativeLedgerOperation;
    private volatile Map<String,Object> lastNativeLedger=Collections.emptyMap();
    private static boolean nativeObserverAvailable(){try{return org.openpnp.scripting.Scripting.hasNativeActionObserverApi();}catch(NoSuchMethodError stockRuntime){return false;}}
    private Job job;
    private volatile boolean jobCountsDirty=true;
    private int cachedRequested,cachedPlaced;
    private long jobCountRecomputations,machineSnapshotRefreshes,lastSnapshotNanos;
    private final java.beans.PropertyChangeListener jobCountListener=event->{jobCountsDirty=true;};
    private String jobId;
    private long jobRevision;
    private NativeControllerDiagnostic controllerDiagnostic;
    private final NativeControllerJournal controllerJournal=new NativeControllerJournal();
    private final NativeTopologyJournal topologyJournal=new NativeTopologyJournal();
    private final NativeInspectionJournal inspectionJournal=new NativeInspectionJournal();
    private final Set<CompletableFuture<Map<String,Object>>> inspectionCompletions=ConcurrentHashMap.newKeySet();
    private PendingInspection pendingInspection;
    /** Private live authority is never serialized or reconstructed from a receipt. */
    private static final class PendingInspection {
        final String id,requestOperation,loadedBoard;
        final long owner,deadline;
        final NativeLoadedBoardInspection.Snapshot captured;
        final Map<String,Object> task;
        boolean active=true,presented,used,intentWritten,receiptWritten,completionDelivered;
        String localOperation,closeReason;
        CompletableFuture<Map<String,Object>> completion;
        final java.util.concurrent.atomic.AtomicReference<CompletableFuture<Map<String,Object>>> queued=new java.util.concurrent.atomic.AtomicReference<>();
        PendingInspection(String id,String operation,String loadedBoard,long owner,NativeLoadedBoardInspection.Snapshot captured,Map<String,Object> task){
            this.id=id;requestOperation=operation;this.loadedBoard=loadedBoard;this.owner=owner;this.captured=captured;this.task=task;
            deadline=System.nanoTime()+TimeUnit.MINUTES.toNanos(5);
        }
    }
    private static final Set<String> CONTROLLER_TOOLS=Set.of("openpnp_get_capabilities","openpnp_get_status","openpnp_get_configuration","openpnp_get_operation","openpnp_get_request_status","openpnp_get_events","openpnp_get_control_session","openpnp_request_control_session","openpnp_renew_control_session","openpnp_release_control_session","openpnp_run_controller_diagnostic");
    private volatile String jobState = "absent";
    private final ArrayList<String> nativeStatus = new ArrayList<>();

    public Bridge(Configuration config, Path tokenFile, Path journalDir, Path sampleRoot,
                  int port, boolean managedSimulator) throws Exception {
        this(config,tokenFile,journalDir,sampleRoot,port,managedSimulator,"native-simulator");
    }
    public Bridge(Configuration config, Path tokenFile, Path journalDir, Path sampleRoot,
                  int port, boolean managedSimulator,String simulatorProfile) throws Exception {
        this(config,tokenFile,journalDir,sampleRoot,port,managedSimulator,simulatorProfile,null);
    }
    public Bridge(Configuration config, Path tokenFile, Path journalDir, Path sampleRoot,
                  int port, boolean managedSimulator,String simulatorProfile,GuiOwnership guiOwnership) throws Exception {
        this(config,tokenFile,journalDir,sampleRoot,port,managedSimulator,simulatorProfile,guiOwnership,null);
    }
    public Bridge(Configuration config, Path tokenFile, Path journalDir, Path sampleRoot,
                  int port, boolean managedSimulator,String simulatorProfile,GuiOwnership guiOwnership,NativePortableLaunch portableLaunch) throws Exception {
        this(config,tokenFile,journalDir,sampleRoot,port,managedSimulator,simulatorProfile,guiOwnership,portableLaunch,null);
    }
    public Bridge(Configuration config,Path tokenFile,Path journalDir,Path sampleRoot,int port,boolean managedSimulator,String simulatorProfile,GuiOwnership guiOwnership,NativePortableLaunch portableLaunch,NativeControllerDiagnostic controllerDiagnostic)throws Exception{
        if(!Arrays.asList("native-simulator","sustained-workload","vacuum-sensing","gui-simulator","adopted-simulator",NativeControllerJournal.PROFILE).contains(simulatorProfile))throw new IllegalArgumentException("Unknown simulator profile");
        if((controllerDiagnostic!=null)!=NativeControllerJournal.PROFILE.equals(simulatorProfile))throw new IllegalArgumentException("Diagnostic profile requires its fresh owned context");
        this.controllerDiagnostic=controllerDiagnostic;if(controllerDiagnostic!=null)controllerDiagnostic.guard(config);
        if((guiOwnership!=null)!= "gui-simulator".equals(simulatorProfile))throw new IllegalArgumentException("GUI profile requires its ownership adapter");
        if((portableLaunch!=null)!= "adopted-simulator".equals(simulatorProfile))throw new IllegalArgumentException("Adopted profile requires its consumed one-time context");
        this.portableLaunch=portableLaunch;if(portableLaunch!=null){portableLaunch.guard(config);portableLaunch.claimJournal(journalDir);}
        this.guiOwnership=guiOwnership;
        this.simulatorProfile=simulatorProfile;
        this.config = config;
        this.machine = config.getMachine();
        this.managedSimulator = managedSimulator && (MainFrame.get() == null || guiOwnership!=null);
        if(guiOwnership!=null)guiOwnership.checkAttachment(config,machine);
        this.journalDir = journalDir.toAbsolutePath();
        this.sampleRoot = sampleRoot.toRealPath();
        this.token = Files.readString(tokenFile, StandardCharsets.UTF_8).trim();
        if (token.length() < 32) throw new IllegalArgumentException("Token must contain at least 32 characters");
        try {
        Files.createDirectories(this.journalDir);
        Path identityFile=this.journalDir.resolve("machine-id");
        if(portableLaunch!=null&&Files.exists(identityFile,LinkOption.NOFOLLOW_LINKS))throw new Fault("ADOPTION_STATE_CONFLICT","Fresh adopted identity file appeared before admission");
        if(!Files.exists(identityFile))Files.writeString(identityFile,UUID.randomUUID().toString(),StandardOpenOption.CREATE_NEW);
        machineId=Files.readString(identityFile,StandardCharsets.UTF_8).trim();
        UUID.fromString(machineId);
        Path file = this.journalDir.resolve("operations.jsonl");
        journal = FileChannel.open(file, portableLaunch==null?StandardOpenOption.CREATE:StandardOpenOption.CREATE_NEW, StandardOpenOption.READ, StandardOpenOption.WRITE);
        journalLock = journal.tryLock();
        if (journalLock == null) throw new IOException("Another bridge owns this journal");
        this.jobDocuments=new NativeJobDocuments(config,this.journalDir.resolve("native-job-documents"),this.journalDir);
        this.jobLineage=new NativeJobLineage((type,payload)->event(type,payload,false));
        this.boardLoads=new NativeBoardLoads((type,payload)->{
            synchronized(Bridge.this){
                event(type,payload);
                String operationId=nativeOperationContext.get();Map<String,Object> op=operations.get(operationId);
                // Binding a prepared job emits nested load records. Those records
                // must not complete the surrounding job/configuration publication.
                boolean enclosingPublication=op!=null&&"canonical-part-binding-publication".equals(op.get("native_effect_kind"))
                    &&Boolean.TRUE.equals(op.get("native_effect_pending"));
                if(op!=null&&!enclosingPublication&&("board_load_intent".equals(type)||"board_load_outcome".equals(type))){op.put("native_effect_pending","board_load_intent".equals(type));op.put("native_effect_kind","simulator-board-load-change");}
            }
        });
        this.materialLoads=new NativeMaterialLoads(config,(type,payload)->{
            synchronized(Bridge.this){event(type,payload);String operationId=nativeOperationContext.get();Map<String,Object> op=operations.get(operationId);
                if(op!=null){op.put("native_effect_pending","material_load_intent".equals(type));op.put("native_effect_kind","simulator-material-load-change");}
            }
        });
        this.faultedJobReplacement=new NativeFaultedJobReplacement(config,materialLoads,boardLoads,jobLineage,this::event,this::terminalReplacementRecoveryCompleted,(nativeJob,attempt)->job==nativeJob&&Objects.equals(jobId,attempt),this::requireRestartOwnership);
        journal.position(journal.size());
        recoverJournal(file);boardLoads.finishRecovery();materialLoads.finishRecovery();if(controllerDiagnostic!=null&&(controllerJournal.hasHistory()||operations.values().stream().anyMatch(op->"openpnp_run_controller_diagnostic".equals(op.get("method")))))controllerDiagnostic.recovered();
        try(java.util.stream.Stream<Path> files=Files.list(this.journalDir)){for(Path artifact:(Iterable<Path>)files::iterator)if(artifact.getFileName().toString().matches("[a-f0-9-]{36}\\.artifact")&&Files.isRegularFile(artifact,java.nio.file.LinkOption.NOFOLLOW_LINKS)){artifactCount++;artifactBytes+=Files.size(artifact);}}
        refresh();
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 16);
        server.setExecutor(httpThreads);
        server.createContext("/health", this::health);
        server.createContext("/rpc", this::rpc);
        statusListener=message -> {
            synchronized (this) {
                nativeStatus.add(message);
                if (nativeStatus.size() > 100) nativeStatus.remove(0);
            }
        };
        machine.getPnpJobProcessor().addTextStatusListener(statusListener);
        watchdog.scheduleAtFixedRate(this::maintenance, 100, 100, TimeUnit.MILLISECONDS);
        }catch(Throwable failure){
            cleanupFailedConstruction();
            if(failure instanceof Exception)throw (Exception)failure;
            if(failure instanceof Error)throw (Error)failure;
            throw new IOException("Bridge setup failed",failure);
        }
    }
    private void cleanupFailedConstruction(){
        closed=true;watchdog.shutdownNow();httpThreads.shutdownNow();
        if(statusListener!=null)machine.getPnpJobProcessor().removeTextStatusListener(statusListener);
        if(job!=null)job.removePropertyChangeListener(jobCountListener);
        if(server!=null)try{server.stop(0);}catch(RuntimeException ignored){}
        if(journalLock!=null)try{journalLock.release();}catch(IOException ignored){}
        if(journal!=null)try{journal.close();}catch(IOException ignored){}
    }

    public void start() { server.start(); }
    public int getPort() { return server.getAddress().getPort(); }
    private void health(HttpExchange x) throws IOException {
        if (!"GET".equals(x.getRequestMethod())) { respond(x, 405, map("error", "method_not_allowed")); return; }
        respond(x, 200, map("status", closed ? "closed" : "ok", "protocol_version", "1.0", "bridge_version", "0.1.0"));
    }
    private void rpc(HttpExchange x) throws IOException {
        if (!"POST".equals(x.getRequestMethod())) { respond(x, 405, map("error", "method_not_allowed")); return; }
        String authorization = x.getRequestHeaders().getFirst("Authorization");
        String origin = x.getRequestHeaders().getFirst("Origin");
        String host = x.getRequestHeaders().getFirst("Host");
        if (origin != null || host == null || !(host.equals("127.0.0.1:"+getPort()) || host.equals("localhost:"+getPort()))) {
            respond(x, 403, map("error", map("code", "ORIGIN_REJECTED", "message", "Loopback non-browser client required"))); return;
        }
        if (authorization == null || !MessageDigest.isEqual(("Bearer "+token).getBytes(StandardCharsets.UTF_8), authorization.getBytes(StandardCharsets.UTF_8))) {
            respond(x, 401, map("error", map("code", "UNAUTHORIZED", "message", "Valid bridge credentials required"))); return;
        }
        try {
            byte[] body = x.getRequestBody().readNBytes(MAX_REQUEST_BYTES + 1);
            if (body.length > MAX_REQUEST_BYTES) throw new Fault("INPUT_TOO_LARGE", "RPC body exceeds 8 MiB");
            checkJsonDepth(body);
            JsonObject input = new JsonParser().parse(new String(body, StandardCharsets.UTF_8)).getAsJsonObject();
            only(input, "method", "params");
            String method = text(input, "method");
            JsonObject params = input.has("params") ? input.getAsJsonObject("params") : new JsonObject();
            respond(x, 200, map("result", call(method, params)));
        } catch (Fault e) {
            respond(x, 200, map("error", map("code", e.code, "message", e.getMessage(), "details", e.details)));
        } catch (Exception e) {
            respond(x, 200, map("error", map("code", "INVALID_REQUEST", "message", safeMessage(e))));
        }
    }
    private static void respond(HttpExchange x, int status, Object value) throws IOException {
        byte[] data = GSON.toJson(plain(value)).getBytes(StandardCharsets.UTF_8);
        x.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        x.getResponseHeaders().set("Cache-Control", "no-store");
        x.sendResponseHeaders(status, data.length);
        try { x.getResponseBody().write(data); } finally { x.close(); }
    }

    public synchronized Object call(String method, JsonObject p) throws Exception {
        if (closed) throw new Fault("BRIDGE_CLOSED", "Bridge is closed");
        drainNativeCompletion();
        if(controllerDiagnostic!=null&&!CONTROLLER_TOOLS.contains(method))throw new Fault("UNSUPPORTED_PROFILE_TOOL","This profile admits only fixed controller diagnostics and receipt access");
        switch (method) {
        case "openpnp_get_capabilities": return capabilities();
        case "openpnp_get_status": return map("bridge_instance_id",instance,"machine_id",machineId, "config_revision", revisionString(),
            "ownership_epoch",epoch,"through_sequence",sequence,"machine",configurationSnapshot(),"job_state",jobState,
            "job_id",jobId,"job_revision",boardLoads.jobRevision(),"board_load_revision",boardLoads.revision(),"board_loads",boardLoads.snapshot(),"material_setup_revision",materialLoads.revision(),"material_loads",materialLoads.snapshot(),"job_progress",jobProgress,"native_action_ledger",lastNativeLedger,"vacuum_sensing",vacuumSources,"vacuum_sensing_journal",vacuumJournal.snapshot(instance),"sensing_reconciliation",sensingReconciliation.summaries(),"faulted_job_replacement",faultedJobReplacement.status(),"gui_ownership",guiOwnership==null?null:guiOwnership.snapshot(),"native_busy",machine.isBusy(),"native_submission",submissionSnapshot(),"active_operation_id",activeOperation,"journal_fault",journalFault,"configuration_fault",configurationFault,"topology_recovery_required",topologyFault,"metrics",metrics(),"native_status",new ArrayList<>(nativeStatus),"controller_diagnostic",controllerDiagnostic==null?null:controllerDiagnostic.cached(),"controller_history",controllerJournal.snapshot());
        case "openpnp_get_configuration": return configurationSnapshot();
        case "openpnp_get_sensing_reconciliation": {
            only(p,"task_id");String id=text(p,"task_id");requireMaterialUuid(id);sensingReconciliationMaintenance();
            Map<String,Object> view=freezeDto(sensingReconciliation.snapshot(id));PendingSensingReconciliation task=pendingSensingReconciliation;
            try{List<Map<String,Object>> replacements=faultedJobReplacement.progressForTask(id);if(!replacements.isEmpty())view.put("job_replacements",replacements);}
            catch(Exception unavailable){view.put("job_replacement_progress_error",map("code","REPLACEMENT_PROGRESS_UNAVAILABLE","message",safeMessage(unavailable),"execution_authority_restored",false));}
            if(task!=null&&task.id.equals(id)){view.put("task_active",task.active&&!task.used);if(task.localOperation!=null)view.put("submission_operation",operation(task.localOperation));}return view;
        }
        case "openpnp_get_board_inspection": {
            only(p,"task_id");String id=text(p,"task_id");requireMaterialUuid(id);
            inspectionMaintenance();Map<String,Object> view=freezeDto(inspectionJournal.get(id,instance));
            PendingInspection task=pendingInspection;
            view.put("task_active",task!=null&&task.id.equals(id)&&task.active&&!task.used);
            view.put("scope_validation","captured at request; rechecked on local submission");
            if(task!=null&&task.id.equals(id)&&task.localOperation!=null){
                if(operations.containsKey(task.localOperation)){
                    Map<String,Object> local=operation(task.localOperation);view.put("submission_operation",local);
                    if("outcome_unknown".equals(local.get("state")))view.put("state","outcome_unknown");
                }else view.put("submission_admission",map("operation_id",task.localOperation,"state","not-committed-to-operation-cache","admission_durability","unconfirmed","journal_fault",journalFault));
            }
            return view;
        }
        case "openpnp_get_board_loads": return boardLoads.snapshot();
        case "openpnp_get_material_loads": only(p);return materialLoads.snapshot();
        case "openpnp_get_operation": return operation(text(p,"operation_id"));
        case "openpnp_get_request_status": {
            String requestId=text(p,"request_id");
            if(sessionReceipts.containsKey(requestId))return map("found",true,"session_receipt",grantReceipt(requestId));
            String id=requests.get(requestId);
            if(id==null&&controllerDiagnostic!=null){Map<String,Object> pending=controllerDiagnostic.pendingRequest(requestId);if(pending!=null)return map("found",true,"uncommitted_admission",pending);}
            return id==null?map("found",false,"request_id",text(p,"request_id")):map("found",true,"operation",operation(id),"command_receipt",commandReceipts.get(text(p,"request_id")));
        }
        case "openpnp_get_events": {
            long after = integer(p,"after_sequence",0,0,Long.MAX_VALUE);
            int limit = (int)integer(p,"limit",100,1,500);
            long oldest=events.isEmpty()?sequence+1:((Number)events.get(0).get("sequence")).longValue();
            if(after<oldest-1)return map("events",Collections.emptyList(),"resync_required",true,"oldest_sequence",oldest,"latest_sequence",sequence);
            List<Map<String,Object>> selected = new ArrayList<>();
            for (Map<String,Object> e : events) if (((Number)e.get("sequence")).longValue()>after && selected.size()<limit) selected.add(e);
            return map("events",selected,"resync_required",false,"through_sequence",selected.isEmpty()?after:selected.get(selected.size()-1).get("sequence"),"latest_sequence",sequence);
        }
        case "openpnp_get_artifact": {
            String id=text(p,"artifact_id");try{UUID.fromString(id);}catch(Exception e){throw new Fault("INVALID_ARGUMENT","Expected artifact UUID");}
            Map<String,Object> artifact=artifacts.get(id);
            Path metadata=journalDir.resolve(id+".metadata.json");
            if(Files.isSymbolicLink(metadata)||Files.isSymbolicLink(journalDir.resolve(id+".artifact")))throw new Fault("ARTIFACT_CORRUPT","Artifact symbolic links are rejected");
            if(artifact==null&&Files.isRegularFile(metadata)){if(Files.size(metadata)>65536)throw new Fault("ARTIFACT_CORRUPT","Artifact metadata exceeds limit");artifact=GSON.fromJson(Files.readString(metadata),Map.class);}
            if(artifact==null)throw new Fault("NOT_FOUND","Unknown artifact");
            if(Files.size(journalDir.resolve(id+".artifact"))>MAX_REQUEST_BYTES)throw new Fault("ARTIFACT_TOO_LARGE","Artifact exceeds 8 MiB");
            byte[] bytes=Files.readAllBytes(journalDir.resolve(id+".artifact"));
            if(!sha256(bytes).equals(artifact.get("sha256")))throw new Fault("ARTIFACT_CORRUPT","Artifact digest does not match");
            Map<String,Object> result=new LinkedHashMap<>(artifact);result.put("base64",Base64.getEncoder().encodeToString(bytes));return result;
        }
        case "openpnp_get_control_session": return sessionSnapshot();
        case "openpnp_request_control_session": {
            simulatorGuard(); if(guiOwnership!=null)guiOwnership.requireRemoteGrant(); checkLease();
            String requestId=text(p,"request_id"),digest=sha256(method+canonical(p));
            if(requests.containsKey(requestId)) {
                if(!digest.equals(requestDigests.get(requestId)))throw new Fault("REQUEST_ID_CONFLICT","Request ID was used with different arguments");
                return grantReceipt(requestId);
            }
            requireRequestCapacity();
            if (session!=null) throw new Fault("CONTROL_OWNED","A session already owns this simulator");
            requireNoPendingSubmission();
            if (activeOperation!=null && !"paused".equals(jobState)) throw new Fault("BUSY","An operation is still active");
            long ttl=integer(p,"ttl_seconds",300,1,600);String granted=UUID.randomUUID().toString();
            Map<String,Object> receipt=map("request_id",requestId,"request_digest",digest,"session_id",granted,"ownership_epoch",epoch+1,"bridge_instance_id",instance,"state","accepted","ttl_seconds",ttl);
            event("session_receipt",receipt);sessionReceipts.put(requestId,receipt);requests.put(requestId,"session:"+requestId);requestDigests.put(requestId,digest);
            session=granted;epoch++;configurationPlans.clear();clearStructurePlans();
            sessionDeadline = System.nanoTime()+TimeUnit.SECONDS.toNanos(ttl);
            event("session_granted", map("session_id",session,"ownership_epoch",epoch));
            receipt.put("state","succeeded");event("session_receipt",receipt);return grantReceipt(requestId);
        }
        case "openpnp_renew_control_session":
            requireSession(p); sessionDeadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(integer(p,"ttl_seconds",300,1,600)); return sessionSnapshot();
        case "openpnp_release_control_session":
            requireSession(p); session=null; epoch++;configurationPlans.clear();clearStructurePlans(); pauseRequested=true; invalidateVacuumReadiness("ownership-change");event("session_released",map("ownership_epoch",epoch)); return sessionSnapshot();
        case "openpnp_reconcile_operation": {
            requireSession(p);Object replay=replayRequest(method,p);if(replay!=null)return replay;String id=text(p,"operation_id");Map<String,Object> op=operations.get(id);
            if(op==null)throw new Fault("NOT_FOUND","Unknown operation");
            if(!"abandon-after-simulator-reset".equals(text(p,"disposition")))throw new Fault("UNSUPPORTED_RECOVERY","Only abandoning a previous simulator instance is supported");
            if(instance.equals(op.get("bridge_instance_id")))throw new Fault("INVALID_STATE","Current instance operations cannot use restart recovery");
            if("cancelled".equals(op.get("state"))){beginCommand(method,p,id);completeCommand(p);return operation(id);}
            if(!"outcome_unknown".equals(op.get("state")))throw new Fault("INVALID_STATE","Operation is not unresolved");
            beginCommand(method,p,id);
            transition(id,"cancelled",map("resolution","abandoned-after-simulator-reset","previous_physical_outcome","unknown","repeat_action_performed",false));
            completeCommand(p);
            return operation(id);
        }
        case "openpnp_plan_motion": {
            requireSession(p); requireIdle(); requireRevision(p);
            only(p,"request_id","session_id","expected_config_revision","tool_id","x","y","z","rotation","units","speed");
            if (!"mm".equals(text(p,"units"))) throw new Fault("INVALID_UNITS","Only explicit mm units supported");
            Map<String,Object> plan=map("kind","motion","config_revision",revisionString(),"ownership_epoch",epoch,
                "tool_id",optionalText(p,"tool_id",defaultTool().getId()),"x",number(p,"x",-200,500),"y",number(p,"y",-200,500),
                "z",number(p,"z",-100,100),"rotation",optionalNumber(p,"rotation",0,-360,360),"speed",optionalNumber(p,"speed",0.1,0.001,1));
            plan.put("backlash_compensation",NativeAxisBacklashSettings.motionBehavior(config));
            String id=storePlan(plan);return map("plan_id",id,"plan",plan,"simulation_only",true,"expires_in_ms",300000);
        }
        case "openpnp_plan_configuration": {
            requireSession(p); requireIdle(); requireRevision(p);
            if (!p.has("changes") || !p.get("changes").isJsonArray() || p.getAsJsonArray("changes").size()>50) throw new Fault("INVALID_CHANGES","Expected up to 50 typed changes");
            requireProfileChanges(p.getAsJsonArray("changes"));
            requireMaterialConfigurationUnenrolled();
            NativeSettings.Patch patch=NativeSettings.stage(config,p.getAsJsonArray("changes"));
            Map<String,Object> plan=map("kind","configuration","config_revision",revisionString(),"ownership_epoch",epoch,"changes",new JsonParser().parse(p.get("changes").toString()),"effects",patch.metadata(),"retained_native_guard",patch.requiresRetainedPlan());
            String id=storePlan(plan);if(patch.requiresRetainedPlan())configurationPlans.put(id,patch);return map("plan_id",id,"plan",plan,"expires_in_ms",300000);
        }
        case "openpnp_pause_job": {
            requireSession(p);Object replay=replayRequest(method,p);if(replay!=null)return replay;requireJobOperation(p);requireRevision(p);
            beginCommand(method,p,activeOperation);pauseRequested=true;event("pause_requested",map("operation_id",activeOperation));completeCommand(p);return operation(activeOperation);
        }
        case "openpnp_abort_job": {
            requireSession(p);Object replay=replayRequest(method,p);if(replay!=null)return replay;requireJobOperation(p);requireRevision(p);String id=activeOperation;beginCommand(method,p,id);abortRequested=true;
            if ("paused".equals(jobState) && pendingSubmission==null) { setJobState("aborting");transition(id,"accepted",null);submitJob(id,epoch); }
            event("abort_requested",map("operation_id",id));completeCommand(p);return operation(id);
        }
        case "openpnp_step_job": {
            if(!p.has("operation_id"))return mutate(method,p);
            only(p,"session_id","request_id","expected_config_revision","operation_id");
            requireSession(p);text(p,"expected_config_revision");Object replay=replayRequest(method,p);if(replay!=null)return replay;requireJobOperation(p);requireRevision(p);
            requireNoPendingSubmission();
            if(!"paused".equals(jobState))throw new Fault("INVALID_STATE","Single-step requires a paused job");
            if(abortRequested)throw new Fault("ABORT_PENDING","Complete the pending abort before stepping");
            if(jobRevision!=revision)throw new Fault("REVISION_CONFLICT","Job configuration changed");
            requireHomed();
            String id=activeOperation;beginCommand(method,p,id);pauseRequested=false;abortRequested=false;setJobState("running");
            transition(id,"accepted",null);submitJob(id,epoch,false,true);completeCommand(p);return operation(id);
        }
        case "openpnp_resume_job": {
            requireSession(p);Object replay=replayRequest(method,p);if(replay!=null)return replay;requireJobOperation(p);requireRevision(p);
            requireNoPendingSubmission();
            if (!"paused".equals(jobState)) throw new Fault("INVALID_STATE","Job is not paused");
            if(abortRequested)throw new Fault("ABORT_PENDING","Complete the pending abort before resuming");
            if (jobRevision!=revision) throw new Fault("REVISION_CONFLICT","Job configuration changed");
            String id=activeOperation;beginCommand(method,p,id);pauseRequested=false;abortRequested=false;setJobState("running");transition(id,"accepted",null);submitJob(id,epoch);completeCommand(p);return operation(id);
        }
        case "openpnp_run_controller_diagnostic": return runControllerDiagnostic(p);
        default: return mutate(method,p);
        }
    }

    private Object runControllerDiagnostic(JsonObject p)throws Exception{
        if(controllerDiagnostic==null)throw new Fault("UNSUPPORTED_PROFILE_TOOL","Launch the dedicated owned-controller diagnostic simulator");
        only(p,"session_id","request_id","expected_config_revision","controller_instance_id");
        requireSession(p);text(p,"expected_config_revision");Object replay=replayRequest("openpnp_run_controller_diagnostic",p);if(replay!=null)return replay;
        requireIdle();requireRevision(p);requireRequestCapacity();
        if(!controllerDiagnostic.id().equals(text(p,"controller_instance_id")))throw new Fault("CONTROLLER_INSTANCE_MISMATCH","Use this fresh observed controller identity");
        String id=UUID.randomUUID().toString(),request=text(p,"request_id"),digest=sha256("openpnp_run_controller_diagnostic"+canonical(p));long owner=epoch;
        // Consume this single generation before admission can write/force ambiguously.
        try{if(!UUID.fromString(request).toString().equals(request))throw new IllegalArgumentException();}catch(IllegalArgumentException invalid){throw new Fault("INVALID_ARGUMENT","Expected canonical request UUID");}
        if(journal.size()>MAX_JOURNAL_BYTES-1024*1024)throw new Fault("JOURNAL_CAPACITY","A fixed diagnostic requires one MiB of journal headroom");
        Map<String,Object> op=map("operation_id",id,"request_id",request,"request_digest",digest,"method","openpnp_run_controller_diagnostic","state","accepted","bridge_instance_id",instance,"config_revision",revisionString(),"controller_instance_id",controllerDiagnostic.id(),"accepted_at",Instant.now().toString());
        try{controllerDiagnostic.consume(op);}catch(IllegalStateException spent){throw new Fault("CONTROLLER_GENERATION_SPENT","This controller generation is spent; start a new isolated diagnostic instance");}
        try{
            event("operation",op);operations.put(id,op);requests.put(request,id);requestDigests.put(request,digest);activeOperation=id;
            event("controller_diagnostic_admission",controllerDiagnostic.admission(id,request,digest,instance,machineId,revisionString(),owner));controllerDiagnostic.admitted();
        }catch(Exception|Error admissionFailure){journalFault=true;throw admissionFailure;}
        try{submitNative(id,owner,false,false,()->{
            synchronized(this){transition(id,"running",null);}
            Map<String,Object> result=controllerDiagnostic.run(id,(type,payload)->event(type,payload),()->{synchronized(this){fence(owner);requireRevision(p);}});
            synchronized(this){finish(id,Boolean.TRUE.equals(result.get("outcome_unknown"))?"outcome_unknown":Boolean.TRUE.equals(result.get("completed_recipe"))?"succeeded":"failed",result);}
            return null;
        },true);}catch(Exception rejected){finish(id,"failed",map("code","NATIVE_ADMISSION_REJECTED","native_effect_started",false,"physical_qualification",false));}
        return operation(id);
    }

    private Object mutate(String method, JsonObject p) throws Exception {
        Set<String> supported = new HashSet<>(Arrays.asList("openpnp_set_machine_enabled","openpnp_home_machine","openpnp_execute_motion",
            "openpnp_capture_camera","openpnp_apply_configuration","openpnp_prepare_job","openpnp_validate_job","openpnp_start_job",
            "openpnp_test_feeder","openpnp_control_actuator","openpnp_change_nozzle_tip","openpnp_backup_configuration","openpnp_restore_configuration","openpnp_export_run_report",
            "openpnp_run_calibration","openpnp_validate_calibration","openpnp_locate_fiducials","openpnp_list_issues"));
        supported.add("openpnp_request_sensing_reconciliation");supported.add("openpnp_measure_sensor");supported.add("openpnp_verify_part_state");supported.add("openpnp_request_board_inspection");supported.add("openpnp_register_material_load");supported.add("openpnp_plan_placement_structure");supported.add("openpnp_apply_placement_structure");supported.add("openpnp_step_job");supported.add("openpnp_export_portable_configuration");supported.add("openpnp_save_job");supported.add("openpnp_load_job");supported.add("openpnp_register_board_load");supported.add("openpnp_inspect_job");supported.add("openpnp_plan_placement_edits");supported.add("openpnp_apply_placement_edits");
        if (!supported.contains(method)) throw new Fault("UNSUPPORTED_OPERATION","No qualified native adapter for "+method);
        requireSession(p);
        if("openpnp_step_job".equals(method)){only(p,"session_id","request_id","expected_config_revision","job_id");text(p,"expected_config_revision");}
        if("openpnp_run_calibration".equals(method)&&isCameraScale(p)){cameraScaleArguments(p);requireMaterialUuid(text(p,"request_id"));}
        if("openpnp_prepare_job".equals(method)&&(p.has("part_bindings")||p.has("canonical_artifact_id")))partBindingArguments(p);
        boolean beginsJob="openpnp_start_job".equals(method)||"openpnp_step_job".equals(method);
        String requestId=text(p,"request_id");
        String digest=sha256(method+canonical(p));
        if (requests.containsKey(requestId)) {
            if (!digest.equals(requestDigests.get(requestId))) throw new Fault("REQUEST_ID_CONFLICT","Request ID was used with different arguments");
            return operation(requests.get(requestId));
        }
        requireRequestCapacity();
        boolean safeDisable="openpnp_set_machine_enabled".equals(method)&&p.has("enabled")&&p.get("enabled").isJsonPrimitive()&&p.getAsJsonPrimitive("enabled").isBoolean()&&!p.get("enabled").getAsBoolean();
        if("openpnp_request_sensing_reconciliation".equals(method)){
            requireMaterialUuid(requestId);String kind=text(p,"recovery_kind");
            boolean replace="replace-faulted-job-attempt".equals(kind);
            boolean continuation=NativeSensingReconciliation.CONTINUATION_KIND.equals(kind);
            boolean restart=NativeSensingReconciliation.RESTART_KIND.equals(kind);
            if(continuation||restart){
                only(p,"session_id","request_id","expected_config_revision","recovery_kind","replacement_attempt_id");requireMaterialUuid(text(p,"replacement_attempt_id"));
            }else if(replace){
                only(p,"session_id","request_id","expected_config_revision","recovery_kind","job_id","expected_job_revision","expected_board_load_revision","expected_material_revision","original_operation_id");
                requireJobEditScope(p);materialLoads.requireRevision(text(p,"expected_material_revision"));requireMaterialUuid(text(p,"original_operation_id"));
            }else{
                only(p,"session_id","request_id","expected_config_revision","recovery_kind");
                if(!"restore-sensing-readiness".equals(kind))throw new Fault("SENSING_RECOVERY_UNSUPPORTED","Unknown local recovery kind");
            }
            requireSensingReconciliationProfile(restart);requireSensingRecoveryIdle(replace||continuation||restart,continuation||restart?text(p,"replacement_attempt_id"):null);
            if(pendingSensingReconciliation!=null&&(pendingSensingReconciliation.active||pendingSensingReconciliation.used&&!pendingSensingReconciliation.completionDelivered))throw new Fault("SENSING_RECOVERY_PENDING","Complete or cancel the current local recovery");
        }else if(safeDisable){requireNoPendingSubmission();if(activeOperation!=null||machine.isBusy())throw new Fault("BUSY","Wait for the current native task before disabling");}else requireIdle();
        requireRevision(p);
        if(isVacuumCommand(method))vacuumArguments(method,p);
        if("openpnp_request_board_inspection".equals(method)){
            only(p,"session_id","request_id","expected_config_revision","job_id","expected_job_revision","expected_board_load_revision","loaded_board_id");
            requireMaterialUuid(requestId);requireJobEditScope(p);text(p,"loaded_board_id");
            requireInspectionProfile();inspectionMaintenance();
            if(pendingInspection!=null&&(pendingInspection.active||pendingInspection.localOperation!=null&&!pendingInspection.completionDelivered))throw new Fault("INSPECTION_PENDING","Finish or cancel the existing local inspection first");
        }
        if("openpnp_export_portable_configuration".equals(method)){text(p,"expected_config_revision");only(p,"session_id","request_id","expected_config_revision");}
        NativeVacuumSensing.Plan sensingPlan=isVacuumCommand(method)?NativeVacuumSensing.admit(config,text(p,"nozzle_id")):null;
        if(sensingPlan!=null)requireVacuumNoFault();
        boolean validJob=!beginsJob||(job!=null&&Objects.equals(jobId,text(p,"job_id"))&&"validated".equals(jobState)&&jobRevision==revision);
        if(beginsJob&&validJob){requireHomed();boardLoads.requireRevision(validatedBoardLoadRevision);}
        if(Set.of("openpnp_inspect_job","openpnp_plan_placement_edits","openpnp_apply_placement_edits","openpnp_plan_placement_structure","openpnp_apply_placement_structure").contains(method))requireJobEditScope(p);
        if("openpnp_register_board_load".equals(method)){text(p,"expected_config_revision");boardLoads.requireRevision(text(p,"expected_board_load_revision"));}
        if("openpnp_register_material_load".equals(method)){
            only(p,"session_id","request_id","expected_config_revision","expected_material_revision","feeder_id","part_id","expected_geometry_sha256","action","expected_load_id");
            for(String field:List.of("session_id","request_id","expected_config_revision","expected_material_revision","feeder_id","part_id","expected_geometry_sha256","action"))if(!p.has(field)||!p.get(field).isJsonPrimitive()||!p.get(field).getAsJsonPrimitive().isString())throw new Fault("INVALID_ARGUMENT","Required JSON string: "+field);
            requireMaterialUuid(requestId);if(p.has("expected_load_id")){if(!p.get("expected_load_id").isJsonPrimitive()||!p.get("expected_load_id").getAsJsonPrimitive().isString())throw new Fault("INVALID_ARGUMENT","expected_load_id must be a UUID string");requireMaterialUuid(text(p,"expected_load_id"));}
            if(!text(p,"expected_geometry_sha256").matches("[0-9a-f]{64}")||!text(p,"expected_material_revision").matches("material-[0-9]+")||!Set.of("bind-existing","replace-full-tray").contains(text(p,"action")))throw new Fault("INVALID_ARGUMENT","Invalid material digest, revision, or action");
            text(p,"expected_config_revision");materialLoads.requireRevision(text(p,"expected_material_revision"));
        }
        final long submittedEpoch=epoch;
        final String id=UUID.randomUUID().toString();
        Map<String,Object> op=map("operation_id",id,"request_id",requestId,"request_digest",digest,"method",method,
            "state","accepted","bridge_instance_id",instance,"config_revision",revisionString(),"accepted_at",Instant.now().toString());
        if(sensingPlan!=null)op.put("vacuum_nozzle_id",text(p,"nozzle_id"));
        if("openpnp_set_machine_enabled".equals(method))op.put("machine_enabled_requested",bool(p,"enabled"));
        if(beginsJob&&validJob){op.put("job_id",jobId);op.put("job_revision",boardLoads.jobRevision());op.put("board_load_revision",boardLoads.revision());op.put("material_setup_revision",materialLoads.revision());op.put("job_lineage",lineageAdmissionFacts());}
        if(!validJob){op.put("state","failed");op.put("job_admission","rejected-before-native-admission");op.put("requested_job_id",text(p,"job_id"));op.put("result",map("code","JOB_NOT_VALIDATED","message","Prepare and validate the selected job first"));}
        event("operation",op);
        operations.put(id,op); if(sensingPlan!=null)vacuumPlans.put(id,sensingPlan); requests.put(requestId,id); requestDigests.put(requestId,digest);if(!validJob)return operation(id);activeOperation=id;
        if (beginsJob) {
            if (job==null || !Objects.equals(jobId,text(p,"job_id")) || !"validated".equals(jobState) || jobRevision!=revision) {
                finish(id,"failed",map("code","JOB_NOT_VALIDATED","message","Prepare and validate the selected job first")); return operation(id);
            }
            try{boardLoads.requireRevision(validatedBoardLoadRevision);}catch(Fault e){finish(id,"failed",map("code",e.code,"message",e.getMessage()));return operation(id);}
            setJobState("running"); pauseRequested=false; abortRequested=false;
            submitJob(id,submittedEpoch,false,"openpnp_step_job".equals(method)); return operation(id);
        }
        try { submitNative(id,submittedEpoch,false,false,() -> {
            try {
                nativeOperationContext.set(id);
                synchronized (this) { fence(submittedEpoch); transition(id,"running",null); }
                Object result=nativeAction(()->execute(method,p));
                if (machine.isEnabled()) machine.getMotionPlanner().waitForCompletion(null,CompletionType.WaitForStillstand);
                synchronized (this) { refresh(); finish(id,"succeeded",result); }
            } catch (GuiOwnership.ScriptRejected e) {
                settleUncertainFailure(id,"SCRIPT_POLICY_REJECTED",e);
            } catch (ConfigurationMutationFence e) {
                settleUncertainFailure(id,"CONFIGURATION_FAULT",e);
            } catch (org.openpnp.machine.reference.VacuumSensing.ObserverFailure e) {
                settleUncertainFailure(id,"VACUUM_OUTCOME_UNKNOWN",e);
            } catch (NativeVacuumJournal.Fence e) {
                settleUncertainFailure(id,e.code,e);
            } catch (Exception e) {
                settleFailure(id,e);
            } finally {synchronized(this){vacuumPlans.remove(id);}nativeOperationContext.remove();}
            return null;
        },true); }catch(Exception|Error admissionFailure){synchronized(this){if(pendingSubmission==null||!pendingSubmission.entered)finish(id,"failed",map("code","NATIVE_ADMISSION_REJECTED","message",safeMessage(admissionFailure),"native_effect_started",false));}}
        return operation(id);
    }

    private Object execute(String method, JsonObject p) throws Exception {
        switch (method) {
        case "openpnp_measure_sensor": case "openpnp_verify_part_state": return executeVacuum(method,p);
        case "openpnp_request_sensing_reconciliation": return captureSensingReconciliation(p);
        case "openpnp_request_board_inspection": return captureBoardInspection(p);
        case "openpnp_set_machine_enabled": {
            boolean enabled=bool(p,"enabled");boolean marked=vacuumLifecycleBoundary(p,!enabled,false);
            beginNativeEffect(enabled?"machine-enable":"machine-disable");vacuumLifecycleBoundary(p,!enabled,marked);
            machine.setEnabled(enabled);completeNativeEffect();return map("enabled",machine.isEnabled());
        }
        case "openpnp_home_machine": requireEnabled();vacuumLifecycleBoundary(p,false,false);beginNativeEffect("home");vacuumLifecycleBoundary(p,false,false);machine.home();completeNativeEffect();return map("homed",machine.isHomed());
        case "openpnp_execute_motion": {
            requireHomed(); Map<String,Object> plan=takePlan(p,"motion");
            HeadMountable tool=tool((String)plan.get("tool_id"));
            Location pose=new Location(LengthUnit.Millimeters,(double)plan.get("x"),(double)plan.get("y"),(double)plan.get("z"),(double)plan.get("rotation"));
            beginNativeEffect("motion");MovableUtils.moveToLocationAtSafeZ(tool,pose,(double)plan.get("speed"));
            machine.getMotionPlanner().waitForCompletion(tool,CompletionType.WaitForStillstand);completeNativeEffect();
            return map("pose",pose(tool.getLocation()),"completion",map("controller_barrier","simulator-standstill","position_source","native-simulation","physical_inspection","not-performed"));
        }
        case "openpnp_capture_camera": {
            Camera camera=camera(optionalText(p,"camera_id",machine.getDefaultHead().getDefaultCamera().getId()));
            String mode=optionalText(p,"mode","raw");Map<String,Object> admission=NativeCameraSettling.admitCapture(camera,mode);BufferedImage image;long started=System.nanoTime();
            if ("raw".equals(mode)) image=camera.captureRaw();
            else {requireEnabled();beginNativeEffect("camera-settle-and-capture");image=camera.settleAndCapture();completeNativeEffect();}
            Map<String,Object> metadata=NativeCameraSettling.captureReceipt(camera,mode,(System.nanoTime()-started)/1000000,image,admission);
            metadata.putAll(map("camera_id",camera.getId(),"width",image.getWidth(),"height",image.getHeight(),"captured_at",Instant.now().toString()));
            ByteArrayOutputStream out=new ByteArrayOutputStream();ImageIO.write(image,"PNG",out);
            return artifact("image/png",out.toByteArray(),metadata);
        }
        case "openpnp_apply_configuration": {
            requireMaterialConfigurationUnenrolled();
            Map<String,Object> plan=takePlan(p,"configuration");
            requireProfileChanges((com.google.gson.JsonArray)plan.get("changes"));
            NativeSettings.Patch patch=takeConfigurationPatch(p,plan);
            final boolean sensing=patch.isVacuumSensingChange();
            final boolean mapped=patch.requiresRetainedPlan()&&!sensing;
            final boolean cameraGeometry=patch.metadata().stream().anyMatch(e->"set_camera_geometry".equals(e.get("type")));
            final boolean assembly=patch.metadata().stream().anyMatch(e->NativeNozzleAssembly.TYPE.equals(e.get("type")));
            final String axisReason=assembly?"nozzle-assembly-topology":patch.metadata().stream().anyMatch(e->NativeAxisBacklashSettings.TYPE.equals(e.get("type")))?"axis-backlash":"mapped-axis-geometry";
            patch.validateCurrentState();
            Map<String,Object> recovery=assembly?topologyRecoveryPreimage():null;
            if(sensing)beginNativeEffect("vacuum-sensing-configuration-model");
            if(mapped||cameraGeometry)beginNativeEffect(mapped?(assembly?"nozzle-assembly-configuration-model":axisReason.equals("axis-backlash")?"axis-backlash-configuration-model":"mapped-axis-configuration-model"):"camera-geometry-configuration-model");
            try {
                beginConfigurationChange();
                if(mapped)invalidateAxisDependencies(axisReason);
                else if(cameraGeometry)invalidateRegistrationDependencies("camera-geometry");
                config.save();patch.apply();config.save();
                Map<String,Object> result=map("config_revision",revisionString(),"persisted",true,"configuration_root",config.getConfigurationDirectory().getAbsolutePath());
                if(assembly){
                    result.put("created_assembly",patch.result());result.put("recovery_preimage",recovery);
                    event("topology_model_persisted",map("schema_version",1,"profile",NativeTopologyJournal.PROFILE,"operation_id",nativeOperationContext.get(),"submission_id",submissionContext.get().token,"result",result));
                }
                if(mapped||cameraGeometry||sensing)completeNativeEffect();
                return result;
            }catch(Exception e){if(assembly)topologyFault=true;configurationFailed(e);throw e;}
            catch(Error e){if(!mapped&&!cameraGeometry&&!sensing)throw e;if(assembly)topologyFault=true;configurationFailed(e);throw new ConfigurationMutationFence(e);}
        }
        case "openpnp_inspect_job": {
            requireJobEditScope(p);Map<String,Object> inspected;
            try{inspected=NativePlacementEdits.inspect(config,job,(int)integer(p,"offset",0,0,100000),(int)integer(p,"limit",100,1,200));}catch(NativePlacementEdits.Fault e){throw new Fault(e.code,e.getMessage());}
            if(p.has("expected_source_fingerprint")&&!Objects.equals(text(p,"expected_source_fingerprint"),inspected.get("source_fingerprint")))throw new Fault("JOB_SNAPSHOT_STALE","Native graph or placement progress changed between pages");
            inspected.put("job_lineage",lineageReadback());inspected.put("job_id",jobId);inspected.put("job_revision",boardLoads.jobRevision());inspected.put("board_load_revision",boardLoads.revision());return inspected;
        }
        case "openpnp_plan_placement_edits": {
            requireJobEditScope(p);
            if(!p.has("changes")||!p.get("changes").isJsonArray())throw new Fault("INVALID_CHANGES","Expected typed native placement changes");
            NativePlacementEdits.Patch patch;
            try{patch=NativePlacementEdits.stage(config,job,p.getAsJsonArray("changes"));}catch(NativePlacementEdits.Fault e){throw new Fault(e.code,e.getMessage());}
            Map<String,Object> plan=map("kind","placement-edits","config_revision",revisionString(),"ownership_epoch",epoch,"job_id",jobId,"job_revision",boardLoads.jobRevision(),"board_load_revision",boardLoads.revision(),"effects",patch.preview());
            if(GSON.toJson(plain(plan)).getBytes(StandardCharsets.UTF_8).length>MAX_JOURNAL_RECORD_BYTES-65536)throw new Fault("PREVIEW_TOO_LARGE","Split placement edits into smaller independently reviewed plans");
            String id=storePlan(plan);placementPlans.put(id,patch);return map("plan_id",id,"plan",plan,"expires_in_ms",300000,"simulation_only",true);
        }
        case "openpnp_apply_placement_edits": {
            requireJobEditScope(p);requireEmptyNozzles();
            Map<String,Object> plan=takePlan(p,"placement-edits");String id=text(p,"plan_id");
            if(!Objects.equals(jobId,plan.get("job_id"))||!Objects.equals(boardLoads.jobRevision(),plan.get("job_revision"))||!Objects.equals(boardLoads.revision(),plan.get("board_load_revision")))throw new Fault("PLAN_STALE","Native job or load changed after placement preview");
            NativePlacementEdits.Patch patch=placementPlans.remove(id);discardStructurePlan(id);configurationPlans.remove(id);if(patch==null)throw new Fault("PLAN_CONSUMED","Placement plan is expired or already attempted");
            boolean changes=patch.preview().get("changed").getAsBoolean();
            if(changes){lineageContentWillChange();setJobState("prepared");validatedBoardLoadRevision=null;jobCountsDirty=true;}
            Map<String,Object> result;
            try{result=patch.apply();}
            catch(NativePlacementEdits.Fault e){
                if(e.code.equals("PLACEMENT_COMMIT_FAILED")||e.code.equals("PLACEMENT_ROLLBACK_FAILED")){synchronized(Bridge.this){configurationFailed(e);String opId=nativeOperationContext.get();if(operations.containsKey(opId)){operations.get(opId).put("native_effect_pending",true);operations.get(opId).put("native_effect_kind","native-placement-publication");}}}
                throw new Fault(e.code,e.getMessage());
            }
            if(Boolean.TRUE.equals(result.get("changed"))){try{boardLoads.jobChanged(job,jobId);plans.clear();planDeadlines.clear();placementPlans.clear();clearStructurePlans();configurationPlans.clear();clearStructurePlans();publishGuiJob();}catch(Exception failure){configurationFailed(failure);throw failure;}}
            result.put("job",jobSummary());result.put("job_revision",boardLoads.jobRevision());result.put("board_load_revision",boardLoads.revision());return result;
        }
        case "openpnp_plan_placement_structure": return planStructure(p);
        case "openpnp_apply_placement_structure": return applyStructure(p);
        case "openpnp_register_material_load": {
            if(!materialProfileAvailable())throw new Fault("MATERIAL_PROFILE_UNSUPPORTED","Use the finite-tray simulator profile or the attested local sensing simulator");
            if(Set.of("running","paused","pause_requested","aborting").contains(jobState))throw new Fault("INVALID_STATE","Complete or abandon job before virtual tray replacement");
            requireEmptyNozzles();
            Object result=materialLoads.register(text(p,"feeder_id"),text(p,"part_id"),text(p,"expected_geometry_sha256"),text(p,"action"),optionalText(p,"expected_load_id",null),text(p,"expected_material_revision"));
            synchronized(this){validatedMaterialRevision=null;if(job!=null)setJobState("prepared");plans.clear();planDeadlines.clear();configurationPlans.clear();}
            return result;
        }
        case "openpnp_register_board_load": {
            if(job==null||!Objects.equals(jobId,text(p,"job_id")))throw new Fault("NO_JOB","Select the prepared native job");
            requireEmptyNozzles();
            // The helper validates completely before its durable change intent. That intent
            // marks this operation uncertain if a native setter/listener fails afterward.
            lineageContentWillChange();
            Object result=boardLoads.change(job,text(p,"root_instance_id"),text(p,"action"),text(p,"side"),optionalText(p,"expected_load_id",null),text(p,"expected_board_load_revision"),false);
            synchronized(this){setJobState("prepared");validatedBoardLoadRevision=null;jobCountsDirty=true;plans.clear();planDeadlines.clear();placementPlans.clear();clearStructurePlans();configurationPlans.clear();clearStructurePlans();}
            publishGuiJob();return result;
        }
        case "openpnp_prepare_job": {
            requireEmptyNozzles();
            if(p.has("part_bindings"))return prepareBoundCanonicalJob(p);
            if(p.has("canonical_job")) {
                if(p.has("sample"))throw new Fault("INVALID_ARGUMENT","Select canonical_job or sample");
                replaceJob(CanonicalJobImporter.load(config,p.getAsJsonObject("canonical_job")));
                synchronized(this){revision++;jobId=UUID.randomUUID().toString();setJobState("prepared");jobRevision=revision;}
                try{config.save();}catch(Exception e){configurationFailed(e);throw e;}synchronized(this){jobLineage.createFresh(jobId,p.has("canonical_job")?"canonical-simulator":"pinned-sample-simulator");}boardLoads.bindJob(job,jobId,true);publishGuiJob();return jobSummary();
            }
            if (!"pnp-test".equals(text(p,"sample"))) throw new Fault("UNSUPPORTED_IMPORT","Only the pinned native pnp-test sample is enabled in this slice");
            Path file=copySample(sampleRoot,config.getConfigurationDirectory().toPath()).resolve("pnp-test.job.xml");
            replaceJob(config.loadJob(file.toFile())); synchronized(this) {jobId=UUID.randomUUID().toString();setJobState("prepared");jobRevision=revision;}synchronized(this){jobLineage.createFresh(jobId,p.has("canonical_job")?"canonical-simulator":"pinned-sample-simulator");}boardLoads.bindJob(job,jobId,true);publishGuiJob();
            return jobSummary();
        }
        case "openpnp_export_portable_configuration": {
            requireEmptyNozzles();
            Path pending=journalDir.resolve(".portable-pending-"+UUID.randomUUID()+".zip");
            try {
                Map<String,Object> exported=NativePortableConfiguration.export(config,pending);
                byte[] bytes=NativePortableConfiguration.read(pending,MAX_REQUEST_BYTES);
                Map<String,Object> file=artifact("application/zip",bytes,map("kind","portable-native-configuration","scope","fresh-native-simulator-adoption","upstream_commit",UPSTREAM,"manifest_in_archive",true,"scripts_activated",false,"in_place_restore",false,"physical_state_transferred",false));
                return map("artifact",file,"manifest",exported.get("manifest"),"first_launch_only",true,"requires_separate_validation_jvm",true,"native_model_limits",NativePortableLimits.describe());
            }finally{Files.deleteIfExists(pending);}
        }
        case "openpnp_save_job": {
            if(job==null)throw new Fault("NO_JOB","Prepare a native job first");
            if(documentArtifacts.size()>=128)throw new Fault("DOCUMENT_CAPACITY","Native document artifact receipt capacity reached");
            synchronized(this){setJobState("prepared");}
            Map<String,Object> lineage=lineageReadback();NativeJobDocuments.LineageMetadata metadata=null;
            if("known".equals(lineage.get("status"))){Map<String,Object> facts=lineageAdmissionFacts();metadata=new NativeJobDocuments.LineageMetadata((String)facts.get("lineage_id"),((Number)facts.get("lineage_revision")).longValue());}
            NativeJobDocuments.Saved saved=jobDocuments.save(job,metadata);
            if(metadata!=null)synchronized(this){jobLineage.bindDocument(jobId,saved.sha256);}
            Map<String,Object> artifact=artifact("application/zip",saved.bytes,map("kind","native-job-document","native_bundle_id",saved.sha256,"manifest_in_archive",true,"entry_count",((List<?>)saved.manifest.get("entries")).size()));
            documentArtifacts.put((String)artifact.get("artifact_id"),saved.sha256);
            return map("artifact",artifact,"job",jobSummary(),"native_format","openpnp-job-with-linked-definitions","transient_registration_preserved",false,"reload_scope","same-owned-document-store-and-unchanged-part-package-definitions");
        }
        case "openpnp_load_job": {
            requireEmptyNozzles();
            String artifactId=text(p,"artifact_id"),bundleId=documentArtifacts.get(artifactId);
            if(bundleId==null){
                JsonObject lookup=new JsonObject();lookup.addProperty("artifact_id",artifactId);
                Map<?,?> retained=(Map<?,?>)call("openpnp_get_artifact",lookup);
                if(!"application/zip".equals(retained.get("mime_type")) || !(retained.get("metadata") instanceof Map))throw new Fault("DOCUMENT_NOT_FOUND","Artifact is not a retained native job document");
                Map<?,?> metadata=(Map<?,?>)retained.get("metadata");Object candidate=metadata.get("native_bundle_id");
                if(!"native-job-document".equals(metadata.get("kind")) || !(candidate instanceof String) || !((String)candidate).matches("[a-f0-9]{64}") || !candidate.equals(retained.get("sha256")))throw new Fault("DOCUMENT_NOT_FOUND","Native document artifact receipt is invalid");
                bundleId=(String)candidate;
            }
            synchronized(this){if(job!=null)setJobState("prepared");}
            NativeJobDocuments.Reloaded loaded=jobDocuments.reloadWithMetadata(bundleId);Job restored=loaded.job;
            String restoredJobId=UUID.randomUUID().toString();
            if(loaded.lineage.isPresent()){NativeJobDocuments.LineageMetadata metadata=loaded.lineage.get();synchronized(this){jobLineage.bindReload(restoredJobId,bundleId,metadata.lineageId,metadata.lineageRevision);}}
            synchronized(this){replaceJob(restored);jobId=restoredJobId;setJobState("prepared");jobRevision=revision;}boardLoads.bindJob(job,jobId,false);publishGuiJob();
            return map("job",jobSummary(),"artifact_id",artifactId,"native_bundle_id",bundleId,"placed_history_restored",true,"registration","invalidated","requires_validation",true,"physical_qualification",false);
        }
        case "openpnp_validate_job": {
            requireHomed(); if(job==null) throw new Fault("NO_JOB","Prepare a job first");
            boardLoads.requireReady(job);materialLoads.requireReady();
            requireVacuumNoFault();NativeVacuumSources.Admission sensing=NativeVacuumSources.admitJob(config,job);
            Map<String,Object> result=NativeJobPreflight.validate(config,job);
            vacuumJobAdmission=Boolean.TRUE.equals(result.get("valid"))?sensing:null;
            synchronized(this){setJobState(Boolean.TRUE.equals(result.get("valid"))?"validated":"prepared");jobRevision=revision;validatedBoardLoadRevision=Boolean.TRUE.equals(result.get("valid"))?boardLoads.revision():null;}
            validatedMaterialRevision=Boolean.TRUE.equals(result.get("valid"))?materialLoads.revision():null;result.put("material_setup_revision",materialLoads.revision());result.put("board_load_revision",boardLoads.revision());result.put("job_revision",boardLoads.jobRevision());result.put("job",jobSummary());return result;
        }
        case "openpnp_test_feeder": {
            if(materialLoads.enrolled(text(p,"feeder_id")))throw new Fault("MATERIAL_MANUAL_FEED_UNSUPPORTED","Enrolled feeders require the instrumented native job path");
            requireHomed(); Feeder feeder=machine.getFeeder(text(p,"feeder_id")); if(feeder==null) throw new Fault("NOT_FOUND","Unknown feeder");
            Nozzle nozzle=nozzle(optionalText(p,"nozzle_id",machine.getDefaultHead().getDefaultNozzle().getId()));
            beginNativeEffect("feed");event("feed_intent",map("feeder_id",feeder.getId())); feeder.feed(nozzle); event("feed_complete",map("feeder_id",feeder.getId()));completeNativeEffect();
            return map("feeder_id",feeder.getId(),"pick_location",pose(feeder.getPickLocation()),"material_consumption","simulated-one-feed");
        }
        case "openpnp_control_actuator": {
            requireEnabled(); Actuator a=null; for(Actuator candidate:machine.getAllActuators()) if(candidate.getId().equals(text(p,"actuator_id"))) a=candidate;
            if(a==null) throw new Fault("NOT_FOUND","Unknown actuator");boolean enabled=bool(p,"enabled");beginNativeEffect("actuator");a.actuate(enabled);completeNativeEffect();
            return map("actuator_id",a.getId(),"last_command",a.getLastActuationValue(),"independent_feedback",false);
        }
        case "openpnp_change_nozzle_tip": {
            requireHomed(); Nozzle nozzle=nozzle(text(p,"nozzle_id")); String tip=optionalText(p,"nozzle_tip_id","");
            if(!((ReferenceNozzle)nozzle).isChangerEnabled()&&!Objects.equals(nozzle.getNozzleTip()==null?"":nozzle.getNozzleTip().getId(),tip))throw new Fault("MANUAL_CHANGE_REQUIRED","The native default fixture has a manual nozzle changer; automatic changes require a configured native automatic changer");
            NozzleTip target=tip.isEmpty()?null:machine.getNozzleTip(tip);if(!tip.isEmpty()&&target==null)throw new Fault("NOT_FOUND","Unknown nozzle tip");
            synchronized(this){if(job!=null)setJobState("prepared");}
            beginNativeEffect("nozzle-tip-change");if(target==null)nozzle.unloadNozzleTip();else nozzle.loadNozzleTip(target,false);completeNativeEffect();
            return map("nozzle_id",nozzle.getId(),"nozzle_tip_id",nozzle.getNozzleTip()==null?null:nozzle.getNozzleTip().getId());
        }
        case "openpnp_run_calibration": {
            requireHomed();
            if(isCameraScale(p))return measureCameraScale(p);
            if(!"nozzle-tip-runout".equals(text(p,"recipe_id")))throw new Fault("UNSUPPORTED_RECIPE","Only native nozzle-tip-runout calibration is available");
            ReferenceNozzle nozzle=(ReferenceNozzle)nozzle(text(p,"nozzle_id"));ReferenceNozzleTip tip=nozzle.getCalibrationNozzleTip();
            if(tip==null)throw new Fault("NO_NOZZLE_TIP","Load a calibration-capable nozzle tip first");
            ReferenceNozzleTipCalibration calibration=tip.getCalibration();
            if(!calibration.isEnabled()&&(!p.has("enable")||!bool(p,"enable")))throw new Fault("CALIBRATION_DISABLED","Explicitly enable the simulated calibration recipe");
            beginConfigurationChange();
            try {
                if(!calibration.isEnabled())calibration.setEnabled(true);
                beginNativeEffect("nozzle-tip-calibration");nozzle.calibrate();completeNativeEffect();
                if(!calibration.isCalibrated(nozzle))throw new Fault("CALIBRATION_FAILED","Native routine did not establish calibration");
                config.save();
            }catch(Exception e){configurationFailed(e);throw e;}
            return calibrationResult(nozzle);
        }
        case "openpnp_validate_calibration":return calibrationResult((ReferenceNozzle)nozzle(text(p,"nozzle_id")));
        case "openpnp_locate_fiducials": {
            requireHomed();if(job==null)throw new Fault("NO_JOB","Prepare a job first");boardLoads.requireReady(job);
            synchronized(this){setJobState("prepared");jobRevision=revision;}
            List<PlacementsHolderLocation<?>> locations=new ArrayList<>();
            for(BoardLocation board:job.getBoardLocations())if(board.isEnabled())locations.add(board);
            beginNativeEffect("fiducial-registration");Location end=machine.getFiducialLocator().locateAllPlacementsHolder(locations,null);completeNativeEffect();boardLoads.registrationCompleted(job,revisionString());
            synchronized(this){setJobState("prepared");jobRevision=revision;}
            return map("registration","native-simulator","end_location",end==null?null:pose(end),"job",jobSummary());
        }
        case "openpnp_list_issues": {
            Solutions solutions=((ReferenceMachine)machine).getSolutions();solutions.findIssues();solutions.publishIssues();
            List<Object> issues=new ArrayList<>();for(Solutions.Issue issue:solutions.getIssues())issues.add(map("fingerprint",issue.getFingerprint(),"severity",issue.getSeverity().toString(),"state",issue.getState().toString(),"issue",issue.getIssue(),"solution",issue.getSolution(),"documentation_url",issue.getUri(),"apply_supported",false));
            return map("issues",issues,"config_revision",revisionString(),"automatic_issue_application",false);
        }
        case "openpnp_backup_configuration": {
            config.save(); Map<String,Object> files=new TreeMap<>();
            for(String name:Arrays.asList("machine.xml","parts.xml","packages.xml","boards.xml","panels.xml","vision-settings.xml","script-state.xml")) {
                Path file=config.getConfigurationDirectory().toPath().resolve(name);if(Files.isRegularFile(file))files.put(name,Files.readString(file));
            }
            NativeConfigurationSnapshots.Snapshot typed=NativeConfigurationSnapshots.capture(config);
            Map<String,Object> result=artifact("application/json",GSON.toJson(plain(map("upstream_commit",UPSTREAM,"configuration",files,"typed_snapshot",typed.document,"scope","native-config-files-and-representable-typed-settings","complete_portable_backup",false))).getBytes(StandardCharsets.UTF_8),map("kind","configuration-backup","typed_change_count",((com.google.gson.JsonArray)typed.document.get("typed_changes")).size(),"omission_count",((List<?>)typed.document.get("omissions")).size()));
            typedBackups.put((String)result.get("artifact_id"),typed);while(typedBackups.size()>128)typedBackups.remove(typedBackups.keySet().iterator().next());return result;
        }
        case "openpnp_restore_configuration": {
            requireMaterialConfigurationUnenrolled();
            NativeConfigurationSnapshots.Snapshot backup=typedBackups.get(text(p,"artifact_id"));
            if(backup==null){
                JsonObject lookup=new JsonObject();lookup.addProperty("artifact_id",text(p,"artifact_id"));
                Map<?,?> retained=(Map<?,?>)call("openpnp_get_artifact",lookup);
                if(!"application/json".equals(retained.get("mime_type")) || !(retained.get("metadata") instanceof Map) || !"configuration-backup".equals(((Map<?,?>)retained.get("metadata")).get("kind")))throw new Fault("NOT_FOUND","Artifact is not a retained typed configuration backup");
                byte[] bytes=Base64.getDecoder().decode((String)retained.get("base64"));
                JsonObject envelope=new JsonParser().parse(new String(bytes,StandardCharsets.UTF_8)).getAsJsonObject();
                if(!envelope.has("typed_snapshot")||!envelope.get("typed_snapshot").isJsonObject())throw new Fault("SNAPSHOT_INVALID","Backup has no typed snapshot");
                backup=NativeConfigurationSnapshots.decode(envelope.getAsJsonObject("typed_snapshot"));
            }
            NativeConfigurationSnapshots.Restore restore=NativeConfigurationSnapshots.prepareRestore(config,backup);
            if("sustained-workload".equals(simulatorProfile) && restore.report.get("restored_job_order")!=null && !"Unsorted".equals(restore.report.get("restored_job_order")))throw new Fault("SIMULATOR_PROFILE_MISMATCH","Restored order would violate the sustained Unsorted profile");
            restore.validateCurrentState();
            final boolean backlash=restore.invalidatesAxisDependencies();
            final boolean cameraGeometry=restore.invalidatesCameraRegistration();
            if(backlash||cameraGeometry)beginNativeEffect(backlash?"axis-backlash-restore-model":"camera-geometry-restore-model");
            beginConfigurationChange();
            try{if(backlash)invalidateAxisDependencies("axis-backlash-restore");else if(cameraGeometry)invalidateRegistrationDependencies("camera-geometry-restore");config.save();restore.apply();config.save();if(backlash||cameraGeometry)completeNativeEffect();}
            catch(Exception e){configurationFailed(e);throw e;}
            catch(Error e){if(!backlash&&!cameraGeometry)throw e;configurationFailed(e);throw new ConfigurationMutationFence(e);}
            restore.report.put("config_revision",revisionString());restore.report.put("job",jobSummary());return restore.report;
        }
        case "openpnp_export_run_report": return artifact("application/json",runReportBytes(),map("kind","run-report"));
        default: throw new Fault("UNSUPPORTED_OPERATION","No native handler");
        }
    }

    private static boolean isVacuumCommand(String method){return Set.of("openpnp_measure_sensor","openpnp_verify_part_state").contains(method);}
    private static void vacuumArguments(String method,JsonObject p)throws Exception {
        if("openpnp_measure_sensor".equals(method))only(p,"session_id","request_id","expected_config_revision","nozzle_id","samples");
        else only(p,"session_id","request_id","expected_config_revision","nozzle_id","state");
        requireMaterialUuid(text(p,"request_id"));
        if(!text(p,"expected_config_revision").matches("cfg-[1-9][0-9]*")||!text(p,"nozzle_id").matches("[A-Za-z0-9_.:+-]{1,128}"))throw new Fault("INVALID_ARGUMENT","Canonical revision and nozzle ID required");
        if("openpnp_measure_sensor".equals(method)&&p.has("samples")){
            if(!p.get("samples").isJsonPrimitive()||!p.getAsJsonPrimitive("samples").isNumber())throw new Fault("INVALID_ARGUMENT","Integer samples required");
            try{int n=p.get("samples").getAsBigDecimal().intValueExact();if(n<1||n>32)throw new ArithmeticException();}catch(ArithmeticException bad){throw new Fault("INVALID_ARGUMENT","Samples must be an integer from 1 to 32");}
        }
        if("openpnp_verify_part_state".equals(method)&&!Set.of("part_on","part_off").contains(text(p,"state")))throw new Fault("INVALID_ARGUMENT","Select part_on or part_off");
    }
    private void requireVacuumNoFault()throws Fault {
        try{vacuumJournal.requireNoFault();}catch(NativeVacuumJournal.Fault held){throw new Fault(held.code,held.getMessage());}
    }
    private void requireVacuumEmpty()throws Fault {
        requireVacuumNoFault();
        for(Object item:(List<?>)vacuumJournal.snapshot(instance).get("nozzles")){
            String id=(String)((Map<?,?>)item).get("nozzle_id");
            try{NativeVacuumSensing.Plan p=NativeVacuumSensing.admit(config,id);vacuumJournal.requireUnoccupied(id,NativeVacuumJournal.binding(machineId,instance,revisionString(),id,p.nozzle().getNozzleTip().getId(),p.nozzle().getVacuumSenseActuator().getId(),p.source()));}
            catch(NativeVacuumJournal.Fault blocked){throw new Fault(blocked.code,blocked.getMessage());}
            catch(Fault blocked){throw blocked;}
            catch(Exception unavailable){throw new Fault("NOZZLE_OCCUPANCY_UNRESOLVED","Current native sensing evidence is unavailable for "+id);}
        }
    }
    private synchronized void invalidateVacuumReadiness(String reason)throws Exception {
        if(vacuumJournal.hasHistory())event("vacuum_readiness_invalidated",map("schema_version",1,"profile",NativeVacuumJournal.PROFILE,"machine_id",machineId,"bridge_instance_id",instance,"config_revision",revisionString(),"reason",reason));
    }
    /** Native state listeners can actuate tooling; only a proven inert policy preserves empty evidence. */
    private boolean vacuumLifecycleBoundary(JsonObject arguments,boolean disabling,boolean marked)throws Exception {
        NativeSubmission submission=submissionContext.get();
        synchronized(this){fence(submission.owner);requireRevision(arguments);}
        Exception unsafe=null;
        try{if(NativeVacuumSources.hasConfiguredSensing(config)||vacuumJournal.hasHistory())NativeVacuumSources.requireLifecycleSafe(config);}
        catch(Exception failure){unsafe=failure;}
        synchronized(this){
            fence(submission.owner);requireRevision(arguments);
            if(unsafe!=null){
                if(!disabling)throw new Fault("SENSING_LIFECYCLE_UNSAFE","Native machine-state actuation policy is not qualified for sensing");
                if(!marked){String id=nativeOperationContext.get();Map<String,Object> op=operations.get(id);
                    event("vacuum_lifecycle_uncertain",map("schema_version",1,"profile",NativeVacuumJournal.PROFILE,"machine_id",machineId,"bridge_instance_id",instance,"config_revision",revisionString(),"operation_id",id,"request_id",op.get("request_id"),"transition","disable","reason","machine-state-policy-unsafe"));marked=true;
                }
            }
            fence(submission.owner);requireRevision(arguments);
        }
        return marked;
    }
    private synchronized Map<String,Object> vacuumContext(String operationId,ReferenceNozzle nozzle,String scope)throws Exception {
        Map<String,Object> op=operations.get(operationId);NativeSubmission submission=submissionContext.get();
        if(op==null||submission==null||!submission.operationId.equals(operationId)||!machine.isTask(Thread.currentThread()))throw new Fault("SENSING_CONTEXT_REQUIRED","Current owned native submission required");
        Map<String,Object> context=map("operation_id",operationId,"request_id",op.get("request_id"),"machine_id",machineId,"bridge_instance_id",instance,"config_revision",op.get("config_revision"),"nozzle_id",nozzle.getId(),"scope",scope,"job_context",null);
        if(!scope.equals("manual")){
            Map<?,?> lineage=(Map<?,?>)op.get("job_lineage");Map<String,Object> current=lineageAdmissionFacts();
            if(!Objects.equals(op.get("job_id"),jobId)||!Objects.equals(op.get("job_revision"),boardLoads.jobRevision())||!Objects.equals(op.get("board_load_revision"),boardLoads.revision())||!Objects.equals(op.get("material_setup_revision"),materialLoads.revision())||lineage==null||!Objects.equals(lineage.get("lineage_id"),current.get("lineage_id"))||!Objects.equals(lineage.get("lineage_revision"),current.get("lineage_revision")))throw new Fault("SENSING_CONTEXT_CHANGED","Native job, loads or lineage differ from admitted operation");
            context.put("job_context",map("job_id",jobId,"job_revision",boardLoads.jobRevision(),"board_load_revision",boardLoads.revision(),"material_setup_revision",materialLoads.revision(),"lineage_id",current.get("lineage_id"),"lineage_revision",current.get("lineage_revision")));
        }
        return context;
    }
    private synchronized void validateVacuumRecord(String type,Map<String,Object> payload,String eventInstance,boolean recovering)throws Exception {
        if(type.equals("vacuum_readiness_invalidated")){if(!machineId.equals(payload.get("machine_id")))throw new IOException("Vacuum readiness machine differs");return;}
        if(type.equals("vacuum_lifecycle_uncertain")){
            Map<String,Object> op=operations.get(payload.get("operation_id"));
            if(op==null||!"openpnp_set_machine_enabled".equals(op.get("method"))||!Boolean.FALSE.equals(op.get("machine_enabled_requested"))||!Set.of("accepted","running").contains(op.get("state"))||!Objects.equals(payload.get("request_id"),op.get("request_id"))||!Objects.equals(payload.get("bridge_instance_id"),op.get("bridge_instance_id"))||!Objects.equals(payload.get("bridge_instance_id"),eventInstance)||!Objects.equals(payload.get("machine_id"),machineId)||!Objects.equals(payload.get("config_revision"),op.get("config_revision")))throw new IOException("Vacuum lifecycle record lacks exact disable admission");
            return;
        }
        Map<?,?> c=(Map<?,?>)payload.get("context");Map<String,Object> op=c==null?null:operations.get(c.get("operation_id"));
        if(op==null||!Objects.equals(c.get("request_id"),op.get("local_native_sensing_reconciliation".equals(op.get("method"))?"reconciliation_request_id":"request_id"))||!Objects.equals(c.get("bridge_instance_id"),op.get("bridge_instance_id"))||!Objects.equals(c.get("bridge_instance_id"),eventInstance)||!Objects.equals(c.get("machine_id"),machineId)||!Objects.equals(c.get("config_revision"),op.get("config_revision"))||!Set.of("accepted","running").contains(op.get("state")))throw new IOException("Vacuum record lacks its exact active operation admission");
        if("recovery".equals(c.get("scope"))){
            if(!"local_native_sensing_reconciliation".equals(op.get("method"))||!Objects.equals(((Map<?,?>)c.get("recovery_context")).get("task_id"),op.get("task_id")))throw new IOException("Sensing recovery observation lacks local operation");
            return;
        }
        boolean manual="manual".equals(c.get("scope"));
        if(manual){if(!isVacuumCommand((String)op.get("method"))||!Objects.equals(op.get("vacuum_nozzle_id"),c.get("nozzle_id")))throw new IOException("Vacuum manual operation binding differs");}
        else {
            if(!Set.of("openpnp_start_job","openpnp_step_job").contains(op.get("method")))throw new IOException("Vacuum job scope lacks a native job operation");
            Map<?,?> j=(Map<?,?>)c.get("job_context"),lineage=(Map<?,?>)op.get("job_lineage");
            if(j==null||lineage==null)throw new IOException("Vacuum job admission is missing");
            for(String key:List.of("job_id","job_revision","board_load_revision","material_setup_revision"))if(!Objects.equals(j.get(key),op.get(key)))throw new IOException("Vacuum admitted job context differs: "+key);
            if(!Objects.equals(j.get("lineage_id"),lineage.get("lineage_id"))||NativeJournalJson.integer(j.get("lineage_revision"),0,9007199254740991L)!=NativeJournalJson.integer(lineage.get("lineage_revision"),0,9007199254740991L))throw new IOException("Vacuum admitted lineage differs");
        }
    }
    private Object executeVacuum(String method,JsonObject p)throws Exception {
        vacuumArguments(method,p);final String id=nativeOperationContext.get();final NativeSubmission submission=submissionContext.get();
        final NativeVacuumSensing.Plan plan; synchronized(this){fence(submission.owner);requireRevision(p);requireVacuumNoFault();plan=vacuumPlans.get(id);if(plan==null)throw new Fault("SENSING_PLAN_CONSUMED","Original sensing admission is absent");}
        final Map<String,Object> context=vacuumContext(id,plan.nozzle(),"manual");final boolean[] begun={false};
        NativeVacuumSensing.Guard guard=()->{synchronized(this){fence(submission.owner);requireRevision(p);}plan.validateCurrentState();synchronized(this){fence(submission.owner);requireRevision(p);}};
        org.openpnp.machine.reference.VacuumSensing.Observer observer=(event,nozzle,data)->{
            try{boolean cleanup=event.startsWith("valve.")&&Boolean.FALSE.equals(data.get("enabled"));
            synchronized(this){
                if(!cleanup){guard.check();if(!begun[0]){beginNativeEffect("vacuum-observation");begun[0]=true;}}
                NativeVacuumSources.observe(config,event,nozzle,data,context);
                vacuumJournal.observe(event,nozzle,data,context,this::event);
            }}catch(NativeVacuumJournal.Fence retained){throw retained;}
            catch(Throwable failure){vacuumJournal.invalidate(nozzle.getId(),"observer-fault");throw new NativeVacuumJournal.Fence("VACUUM_OUTCOME_UNKNOWN","Native observation boundary failed",failure);}
        };
        Map<String,Object> result="openpnp_measure_sensor".equals(method)?plan.measure((int)integer(p,"samples",1,1,32),guard,observer):plan.verify(text(p,"state"),guard,observer);
        synchronized(this){guard.check();if(begun[0])completeNativeEffect();result.put("vacuum_sensing_journal",vacuumJournal.snapshot(instance));result.put("config_revision",revisionString());}
        return result;
    }
    private void vacuumJobGuard(String id,long owner,boolean localCleanup)throws Exception {
        synchronized(this){
            if(localCleanup){simulatorGuard();if(owner!=epoch)throw new Fault("OWNERSHIP_REVOKED","Local cleanup superseded");}else fence(owner);
            if(!revisionString().equals(operations.get(id).get("config_revision")))throw new Fault("REVISION_CONFLICT","Job configuration differs from admission");
        }
        if(vacuumJobAdmission==null)throw new Fault("SENSING_JOB_NOT_VALIDATED","Validate the job with current source bindings before native initialization");
        vacuumJobAdmission.validateCurrentState();
        synchronized(this){if(localCleanup){simulatorGuard();if(owner!=epoch)throw new Fault("OWNERSHIP_REVOKED","Local cleanup superseded");}else fence(owner);}
    }
    private org.openpnp.machine.reference.VacuumSensing.Scope observeVacuumJob(String id,long owner,boolean localCleanup)throws Exception {
        vacuumJobGuard(id,owner,localCleanup);requireVacuumNoFault();
        if(vacuumJobAdmission.sensingNozzleIds().isEmpty())return null;
        return org.openpnp.machine.reference.VacuumSensing.observe((event,nozzle,data)->{
            try{boolean cleanup=event.startsWith("valve.")&&Boolean.FALSE.equals(data.get("enabled"));
            if(!cleanup)vacuumJobGuard(id,owner,localCleanup);
            synchronized(this){Map<String,Object> context=vacuumContext(id,nozzle,localCleanup?"cleanup":"job");vacuumJobAdmission.observe(event,nozzle,data,context);vacuumJournal.observe(event,nozzle,data,context,this::event);}
            if(!cleanup)vacuumJobGuard(id,owner,localCleanup);
            }catch(NativeVacuumJournal.Fence retained){throw retained;}
            catch(Throwable failure){vacuumJournal.invalidate(nozzle.getId(),"observer-fault");throw new NativeVacuumJournal.Fence("VACUUM_OUTCOME_UNKNOWN","Native job observation boundary failed",failure);}
        });
    }
    private void vacuumActionEvent(String type,Map<String,Object> payload)throws Exception {
        synchronized(this){
            if(type.equals("native_action_intent")){
                NativeSubmission submission=submissionContext.get();if(submission==null)throw new Fault("SENSING_CONTEXT_REQUIRED","Native action has no submission");
                requireVacuumNoFault();vacuumJobGuard(submission.operationId,submission.owner,submission.localCleanup);
                invalidateVacuumReadiness("native-action");
            }
            event(type,payload);
            if(type.equals("native_action_intent")){NativeSubmission submission=submissionContext.get();vacuumJobGuard(submission.operationId,submission.owner,submission.localCleanup);}
        }
    }

    private static boolean isCameraScale(JsonObject p) {
        return p.has("recipe_id")&&p.get("recipe_id").isJsonPrimitive()&&p.get("recipe_id").getAsJsonPrimitive().isString()
            &&NativeCameraScaleMeasurement.RECIPE.equals(p.get("recipe_id").getAsString());
    }
    private static NativeCameraScaleMeasurement.Arguments cameraScaleArguments(JsonObject p)throws Exception {
        try{return NativeCameraScaleMeasurement.parseArguments(p);}
        catch(NativeCameraScaleMeasurement.Fault refused){throw new Fault(refused.code,refused.getMessage());}
    }
    private static Map<String,Object> cameraScaleRuntimeCapabilities() {
        try{return NativeCameraScaleRuntime.require();}
        catch(Fault unavailable){return map("available",false,"reason",unavailable.code,"physical_qualification",false);}
    }
    private void partBindingArguments(JsonObject p)throws Exception {
        only(p,"session_id","request_id","expected_config_revision","canonical_job","canonical_artifact_id","part_bindings");
        requireMaterialUuid(text(p,"request_id"));
        if(!text(p,"expected_config_revision").matches("cfg-[0-9]+")
                ||!text(p,"canonical_artifact_id").matches("[a-f0-9]{64}")
                ||!p.has("canonical_job")||!p.get("canonical_job").isJsonObject()
                ||!p.has("part_bindings")||!p.get("part_bindings").isJsonArray())
            throw new Fault("INVALID_ARGUMENT","Existing-parts import requires a canonical job, source artifact hash, bindings and current revision");
        if(p.getAsJsonArray("part_bindings").size()<1||p.getAsJsonArray("part_bindings").size()>1000)
            throw new Fault("INVALID_PART_BINDINGS","Expected 1..1000 explicit part bindings");
    }

    private Object prepareBoundCanonicalJob(JsonObject p)throws Exception {
        partBindingArguments(p);
        NativeSubmission submission=submissionContext.get();String operationId=nativeOperationContext.get();
        if(submission==null||!Objects.equals(submission.operationId,operationId))
            throw new Fault("NATIVE_EXECUTOR_REQUIRED","Part-bound import requires an owned native submission");
        final long owner=submission.owner;
        synchronized(this){fence(owner);requireRevision(p);requireEmptyNozzles();}
        JsonObject canonicalJob=p.getAsJsonObject("canonical_job");
        NativePartBindings bindings=NativePartBindings.resolve(config,canonicalJob,p.getAsJsonArray("part_bindings"));
        Job prepared=CanonicalJobImporter.loadExistingParts(config,canonicalJob,bindings);
        Map<String,Object> provenance=map("schema_version",1,"profile",NativePartBindings.PROFILE,
            "operation_id",operationId,"request_id",text(p,"request_id"),"request_digest",sha256("openpnp_prepare_job"+canonical(p)),
            "bridge_instance_id",instance,"machine_id",machineId,"config_revision_before",text(p,"expected_config_revision"),
            "source_artifact_id",text(p,"canonical_artifact_id"),"source_artifact_provenance","adapter-provided; not authenticated supplier identity",
            "canonical_json_sha256",bindings.canonicalSha256(),"resolved_part_count",bindings.provenance().size(),
            "explicit_binding_count",p.getAsJsonArray("part_bindings").size(),"parts",bindings.provenance(),
            "counts_scope","definition placements, not expanded instances or completed actions",
            "part_library_changed",false,"physical_equivalence_verified",false,"publication_authority",false);
        byte[] bytes=GSON.toJson(provenance).getBytes(StandardCharsets.UTF_8);
        Map<String,Object> resolution;
        synchronized(this){
            fence(owner);requireRevision(p);
            resolution=artifact("application/json",bytes,map("kind","canonical-part-resolution","operation_id",operationId,"profile",NativePartBindings.PROFILE));
            String artifactId=(String)resolution.get("artifact_id");
            for(String suffix:List.of(".artifact",".metadata.json"))try(FileChannel channel=FileChannel.open(journalDir.resolve(artifactId+suffix),StandardOpenOption.WRITE,LinkOption.NOFOLLOW_LINKS)){channel.force(true);}
            NativePortableConfiguration.forceDirectory(journalDir);
            event("canonical_part_bindings_staged",map("operation_id",operationId,"part_resolution",resolution,
                "resolved_part_count",bindings.provenance().size(),"source_artifact_id",text(p,"canonical_artifact_id"),"native_job_published",false));
        }
        // The staging receipt is evidence only. Recheck retained models and authority
        // after its durable write, immediately before the publication intent.
        boolean publicationAttempted=false;
        try {
            synchronized(this){
                bindings.validate(config,canonicalJob);fence(owner);requireRevision(p);requireEmptyNozzles();
                publicationAttempted=true;beginNativeEffect("canonical-part-binding-publication");
                // Journal forcing can block long enough for a lease or retained
                // model to change. Keep the last checks and publication together.
                bindings.validate(config,canonicalJob);fence(owner);requireRevision(p);requireEmptyNozzles();
                replaceJob(prepared);revision++;jobId=UUID.randomUUID().toString();setJobState("prepared");jobRevision=revision;
                plans.clear();planDeadlines.clear();placementPlans.clear();clearStructurePlans();configurationPlans.clear();validatedMaterialRevision=null;
            }
            config.save();
            synchronized(this){jobLineage.createFresh(jobId,"canonical-simulator");}
            boardLoads.bindJob(job,jobId,true);publishGuiJob();
            Map<String,Object> result=jobSummary();
            result.put("part_resolution",resolution);result.put("part_binding_profile",NativePartBindings.PROFILE);
            result.put("source_artifact_id",text(p,"canonical_artifact_id"));result.put("canonical_json_sha256",bindings.canonicalSha256());
            result.put("request_digest",provenance.get("request_digest"));result.put("config_revision",revisionString());
            result.put("part_library_changed",false);result.put("physical_equivalence_verified",false);
            completeNativeEffect();return result;
        } catch(Exception|Error failure) {
            if(publicationAttempted)try{configurationFailed(failure);}catch(Exception|Error journalFailure){failure.addSuppressed(journalFailure);}
            throw failure;
        }
    }

    private Object measureCameraScale(JsonObject p)throws Exception {
        NativeCameraScaleMeasurement.Arguments args=cameraScaleArguments(p);
        Map<String,Object> runtime=NativeCameraScaleRuntime.require();
        NativeSubmission submission=submissionContext.get();String operationId=nativeOperationContext.get();
        if(submission==null||!Objects.equals(submission.operationId,operationId))throw new Fault("NATIVE_EXECUTOR_REQUIRED","Camera measurement requires an owned native submission");
        final long owner=submission.owner;
        synchronized(this){fence(owner);requireRevision(p);if(artifactBytes>MAX_ARTIFACT_STORAGE_BYTES-8L*MAX_REQUEST_BYTES)
            throw new Fault("ARTIFACT_STORAGE_CAPACITY","Reserve space for eight bounded camera frames before measurement");}
        Camera selected=camera(args.cameraId);
        try {
            NativeCameraScaleMeasurement.Plan plan=NativeCameraScaleMeasurement.admit(config,selected,args.displacementMm,args.diameterPx);
            event("camera_scale_admission",map("operation_id",operationId,"config_revision",revisionString(),"plan",plan.describe(),"runtime",runtime));
            Map<String,Object> measured=plan.run(new NativeCameraScaleMeasurement.Callbacks(){
                public void checkCurrent()throws Exception {synchronized(Bridge.this){fence(owner);requireRevision(p);requireHomed();
                    if(journalFault||configurationFault)throw new Fault("RECOVERY_REQUIRED","Camera measurement cannot continue through a journal or configuration fault");}}
                public void beginEffect(String kind,Map<String,Object> detail)throws Exception {
                    synchronized(Bridge.this){checkCurrent();beginNativeEffect(kind);event("camera_scale_effect_intent",map("operation_id",operationId,"kind",kind,"detail",detail));}
                }
                public void endEffect(String kind,Map<String,Object> detail)throws Exception {
                    synchronized(Bridge.this){event("camera_scale_effect_outcome",map("operation_id",operationId,"kind",kind,"detail",detail,"physical_outcome_verified",false));completeNativeEffect();}
                }
                public String persistObservation(byte[] png,Map<String,Object> observation)throws Exception {
                    synchronized(Bridge.this){
                        Map<String,Object> file=artifact("image/png",png,map("kind","camera-scale-observation","operation_id",operationId,"config_revision",revisionString(),"observation",observation,"physical_qualification",false));
                        String artifactId=(String)file.get("artifact_id");
                        // Force files and their directory before journaling the observation.
                        // This does not establish filesystem power-loss durability.
                        for(String suffix:List.of(".artifact",".metadata.json"))try(FileChannel channel=FileChannel.open(journalDir.resolve(artifactId+suffix),StandardOpenOption.WRITE)){channel.force(true);}
                        try(FileChannel directory=FileChannel.open(journalDir,StandardOpenOption.READ)){directory.force(true);}
                        event("camera_scale_observation",map("operation_id",operationId,"artifact",file));return artifactId;
                    }
                }
            });
            Map<String,Object> result=new LinkedHashMap<>(measured);
            result.putAll(map("operation_id",operationId,"config_revision",revisionString(),"runtime",runtime,"calibration_applied",false,"physical_qualification",false));
            if("accepted".equals(measured.get("measurement_status"))) {
                Map<?,?> scale=(Map<?,?>)measured.get("proposed_scale_mm_per_px");
                ReferenceCamera camera=(ReferenceCamera)selected;Location offsets=camera.getHeadOffsets().convertToUnits(LengthUnit.Millimeters);
                Map<String,Object> change=map("type","set_camera_geometry","camera_id",args.cameraId,"units_per_pixel_x_mm",scale.get("x"),"units_per_pixel_y_mm",scale.get("y"),
                    "working_plane_z_mm",camera.getUnitsPerPixelPrimary().convertToUnits(LengthUnit.Millimeters).getZ(),
                    "head_offsets",map("x_mm",offsets.getX(),"y_mm",offsets.getY(),"z_mm",offsets.getZ(),"rotation_deg",offsets.getRotation()));
                try {
                    NativeSettings.validate(config,GSON.toJsonTree(change).getAsJsonObject());
                    result.put("proposed_configuration_change",change);result.put("configuration_proposal_status","available");
                    result.put("application","Submit the proposed change through plan_configuration and apply_configuration against this configuration revision; applying it invalidates existing native registration and job validation");
                }catch(Fault unsupported){
                    result.put("configuration_proposal_status","unavailable");
                    result.put("configuration_proposal_error",map("code",unsupported.code,"message",unsupported.getMessage()));
                }
            }
            event("camera_scale_measurement_completed",result);return result;
        }catch(NativeCameraScaleMeasurement.Fault refused){throw new Fault(refused.code,refused.getMessage());}
    }

    private Map<String,Object> calibrationResult(ReferenceNozzle nozzle) {
        ReferenceNozzleTip tip=nozzle.getCalibrationNozzleTip();
        if(tip==null)return map("nozzle_id",nozzle.getId(),"calibrated",false,"reason","no-calibration-tip");
        ReferenceNozzleTipCalibration calibration=tip.getCalibration();boolean valid=calibration.isEnabled()&&calibration.isCalibrated(nozzle);
        List<Object> offsets=new ArrayList<>();if(valid)for(int angle=0;angle<360;angle+=45)offsets.add(map("angle",angle,"offset",pose(calibration.getCalibratedOffset(nozzle,angle))));
        return map("nozzle_id",nozzle.getId(),"nozzle_tip_id",tip.getId(),"enabled",calibration.isEnabled(),"calibrated",valid,
            "method","native-nozzle-tip-runout","native_summary",valid?calibration.getCalibrationInformation(nozzle):null,"configured_angle_subdivisions",calibration.getAngleSubdivisions(),
            "fitted_offsets",offsets,"config_revision",revisionString(),"physical_qualification",false);
    }

    private synchronized byte[] runReportBytes(){
        // Status listeners and operation publication use this same monitor.
        return GSON.toJson(map("job",jobSummary(),"operations",operations,"native_status",nativeStatus,"independently_inspected",0,"hardware_qualified",false)).getBytes(StandardCharsets.UTF_8);
    }

    private <T> T nativeAction(Callable<T> action) throws Exception {return portableLaunch!=null?portableLaunch.invokeNative(action):guiOwnership==null?action.call():guiOwnership.invokeNative(action);}

    private synchronized <T> Future<T> submitNative(String id,long owner,boolean jobTask,boolean localCleanup,Callable<T> task,boolean ignoreEnabled) throws Exception {
        requireNoPendingSubmission();
        NativeSubmission submission=new NativeSubmission(id,owner,jobTask,localCleanup);
        pendingSubmission=submission;
        Callable<T> body=()->{
            synchronized(this){submission.entered=true;submissionContext.set(submission);}
            try{
                synchronized(this){if(submission.publicationAttempted||(submission.future!=null&&submission.future.isCancelled()))throw new Fault("NATIVE_SUBMISSION_FENCED","Native submission was cancelled before its body was admitted");}
                return task.call();
            }
            catch(Exception|Error failure){synchronized(this){submission.bodyFailure=failureFacts(failure);}throw failure;}
            finally{synchronized(this){submission.exited=true;submission.jobPresent=job!=null;submissionContext.remove();}}
        };
        try{
            Future<T> future=guiOwnership==null?machine.submit(body,null,ignoreEnabled):guiOwnership.submitNative(body,ignoreEnabled);
            if(future==null)throw new IllegalStateException("Native admission returned no Future");
            submission.future=future;return future;
        }catch(Exception|Error rejected){
            // Native submit rejects synchronously before admission. A body that did enter
            // cannot be released as a proven pre-admission failure.
            if(!submission.entered)pendingSubmission=null;
            else publicationFailed(submission,rejected);
            throw rejected;
        }
    }

    private synchronized void setJobState(String state){
        NativeSubmission submission=submissionContext.get();
        if(submission==null)jobState=state;else submission.desiredJobState=state;
    }
    private synchronized void revokeJobValidation(){
        // Revocation is conservative and must precede configuration setters. It
        // does not assert that the current native operation has completed.
        jobState="prepared";NativeSubmission submission=submissionContext.get();
        if(submission!=null)submission.desiredJobState="prepared";
    }
    private String bodyJobState(){NativeSubmission submission=submissionContext.get();return submission!=null&&submission.desiredJobState!=null?submission.desiredJobState:jobState;}
    private void requireNoPendingSubmission()throws Fault{if(pendingSubmission!=null)throw new Fault("BUSY","Native task completion or its durable publication remains pending");}
    private static Map<String,Object> failureFacts(Throwable failure){return map("type",failure.getClass().getName(),"message",safeMessage(failure));}
    @SuppressWarnings("unchecked") private static <T> T freezeDto(T value){
        if(value instanceof Map){Map<String,Object> copy=new LinkedHashMap<>();((Map<?,?>)value).forEach((k,v)->copy.put(String.valueOf(k),freezeDto(v)));return (T)copy;}
        if(value instanceof Iterable){List<Object> copy=new ArrayList<>();for(Object item:(Iterable<?>)value)copy.add(freezeDto(item));return (T)copy;}
        if(value instanceof JsonElement)return(T)new JsonParser().parse(value.toString());
        return value;
    }
    private Map<String,Object> knownBodyOutcome(NativeSubmission submission){
        return submission.outcome==null?map("captured",false,"body_failure",submission.bodyFailure):map("captured",true,"state",submission.outcome.get("state"),"result",submission.outcome.get("result"),"job_state",submission.desiredJobState,"body_failure",submission.bodyFailure);
    }
    private Map<String,Object> submissionSnapshot(){
        NativeSubmission submission=pendingSubmission;
        return submission==null?null:map("submission_id",submission.token,"operation_id",submission.operationId,"phase",submission.phase,
            "body_entered",submission.entered,"body_exited",submission.exited,"completion_publication_attempted",submission.publicationAttempted,
            "publication_fault",submission.publicationFault,"completion_observation",submission.completionObservation,"ownership_retained",true);
    }
    private void publicationFailed(NativeSubmission submission,Throwable failure){
        journalFault=true;pauseRequested=true;submission.publicationAttempted=true;submission.phase="publication-fault";
        submission.publicationFault=freezeDto(map("code","NATIVE_COMPLETION_PUBLICATION_FAILED","durable",false,"error",failureFacts(failure),
            "known_body_outcome",knownBodyOutcome(submission),"completion_observation",submission.completionObservation,"repeat_action_performed",false));
    }
    private synchronized void maintenance(){
        if(closed)return;
        try{drainNativeCompletion();}
        catch(Throwable failure){if(pendingSubmission!=null)publicationFailed(pendingSubmission,failure);else{journalFault=true;pauseRequested=true;}}
        finally{checkLease();try{inspectionMaintenance();sensingReconciliationMaintenance();}catch(Throwable failure){journalFault=true;pauseRequested=true;}}
    }
    /** No motion, driver/planner calls, or waiting on an unfinished Future. Recovery resolution
     * additionally compares bounded native model state under its completed publication witness. */
    private synchronized void drainNativeCompletion(){
        NativeSubmission submission=pendingSubmission;
        if(submission==null||submission.future==null||!submission.future.isDone()||submission.publicationAttempted)return;
        Throwable wrapperFailure=null;boolean cancelled=false;
        try{submission.future.get();}
        catch(CancellationException failure){wrapperFailure=failure;cancelled=true;}
        catch(ExecutionException failure){wrapperFailure=failure.getCause()==null?failure:failure.getCause();}
        catch(InterruptedException interrupted){Thread.currentThread().interrupt();return;}
        catch(Throwable failure){wrapperFailure=failure;}
        Map<String,Object> next=submission.outcome==null?freezeDto(operations.get(submission.operationId)):freezeDto(submission.outcome);
        String finalJobState=submission.desiredJobState;
        boolean release=submission.releaseActive;
        if(wrapperFailure!=null||submission.outcome==null){
            next.put("state","outcome_unknown");
            next.put("result",map("code",cancelled?"NATIVE_FUTURE_CANCELLED":wrapperFailure==null?"NATIVE_BODY_OUTCOME_MISSING":"NATIVE_WRAPPER_FAILED",
                "known_body_outcome",knownBodyOutcome(submission),"wrapper_error",wrapperFailure==null?null:failureFacts(wrapperFailure),"repeat_action_performed",false));
            finalJobState=submission.jobPresent?"outcome_unknown":null;release=true;
        }
        if("paused".equals(next.get("state"))&&next.get("result") instanceof Map){((Map<String,Object>)next.get("result")).put("pending_abort",abortRequested);}
        submission.phase=cancelled?"cancelled-ownership-retained":wrapperFailure==null?"completed":"wrapper-failed";
        submission.completionObservation=freezeDto(map("phase",submission.phase,"native_wrapper_completed",!cancelled,
            "native_wrapper_succeeded",wrapperFailure==null,"wrapper_error",wrapperFailure==null?null:failureFacts(wrapperFailure),
            "body_entered",submission.entered,"body_exited",submission.exited));
        next.put("native_completion",map("submission_id",submission.token,"phase",submission.phase,"native_wrapper_completed",!cancelled,
            "native_wrapper_succeeded",wrapperFailure==null,"physical_outcome_verified",false));
        next.put("updated_at",Instant.now().toString());
        Map<String,Object> progress=submission.progress==null?null:freezeDto(submission.progress);
        if(progress!=null)progress.put("state",finalJobState==null?jobState:finalJobState);
        submission.publicationAttempted=true;
        try{
            event("operation",next);
            operations.put(submission.operationId,next);
            completeSensingReconciliation(submission,next,wrapperFailure,cancelled);
            if(finalJobState!=null)jobState=finalJobState;
            if(progress!=null)jobProgress=progress;
            if(!cancelled){
                if(release)releaseOperation(submission.operationId);
                pendingSubmission=null;
            }
        }catch(Throwable failure){publicationFailed(submission,failure);return;}
        // An abort accepted after the body staged its pause must still receive one
        // cleanup submission. Expired ownership leaves the pause held for recovery.
        if(!cancelled&&"paused".equals(next.get("state"))&&abortRequested&&!submission.localCleanup&&session!=null&&epoch==submission.owner&&System.nanoTime()<sessionDeadline){
            try{jobState="aborting";transition(submission.operationId,"accepted",null);submitJob(submission.operationId,epoch);}
            catch(Throwable failure){publicationFailed(submission,failure);pendingSubmission=submission;}
        }
    }

    private void submitJob(String id,long owner) throws Exception {submitJob(id,owner,false);}
    private void submitJob(String id,long owner,boolean localCleanup) throws Exception {submitJob(id,owner,localCleanup,false);}
    private void submitJob(String id,long owner,boolean localCleanup,boolean singleStep) throws Exception {
        try { submitNative(id,owner,true,localCleanup,() -> {
            boolean nativeWorkDispatched=false;
            org.openpnp.machine.reference.VacuumSensing.Scope sensingScope=null;
            try {
                nativeOperationContext.set(id);
                synchronized(this) {
                    if(localCleanup){simulatorGuard();if(owner!=epoch)throw new Fault("OWNERSHIP_REVOKED","Local cleanup was superseded");}
                    else fence(owner);
                    transition(id,"running",null);
                }
                requireHomed();
                if(!localCleanup){boardLoads.requireRevision(validatedBoardLoadRevision);boardLoads.requireReady(job);materialLoads.requireRevision(validatedMaterialRevision);materialLoads.requireReady();}
                vacuumJobGuard(id,owner,localCleanup);requireVacuumNoFault();
                sensingScope=observeVacuumJob(id,owner,localCleanup);
                if(nativeLedgerAvailable && nativeLedger==null){Map<String,Object> loadScope=boardLoads.ledgerScope(job);nativeLedger=new NativeActionLedger(this::vacuumActionEvent,id,jobId,revisionString(),(String)loadScope.get("scope_id"),job,loadScope).withMaterialObserver(materialLoads);nativeLedgerOperation=id;lastNativeLedger=nativeLedger.snapshot();}
                if(nativeLedger!=null&&!id.equals(nativeLedgerOperation))throw new IllegalStateException("Another native action ledger owns the job");
                if(!Boolean.TRUE.equals(operation(id).get("initialized"))) {
                    nativeWorkDispatched=true;
                    nativeAction(()->{machine.getPnpJobProcessor().initialize(job);return null;});
                    synchronized(this) {operations.get(id).put("initialized",true);}
                }
                boolean singleStepComplete=false;
                while(true) {
                    synchronized(this) {
                        if(abortRequested) {setJobState("aborting");break;}
                        if(pauseRequested || owner!=epoch || session==null || System.nanoTime()>=sessionDeadline) {setJobState("pause_requested");break;}
                        if(journalFault)throw new Fault("JOURNAL_FAULT","Journal unavailable");
                        if(!localCleanup)boardLoads.requireRevision(validatedBoardLoadRevision);
                        if(guiOwnership!=null)simulatorGuard();
                    }
                    vacuumJobGuard(id,owner,localCleanup);requireVacuumNoFault();
                    nativeWorkDispatched=true;
                    long step;
                    synchronized(this){Map<String,Object> op=operations.get(id);step=((Number)op.getOrDefault("native_steps_started",0)).longValue()+1;event("native_step_intent",map("operation_id",id,"step_index",step));op.put("native_steps_started",step);op.put("native_effect_pending",true);op.put("native_effect_kind","native-job-step");}
                    boolean more;
                    if(nativeLedger!=null){
                        try(NativeActionLedger.StepScope scope=nativeLedger.openStep(step)){more=nativeAction(()->machine.getPnpJobProcessor().next());scope.complete();}
                        finally{lastNativeLedger=nativeLedger.snapshot();}
                    }else more=nativeAction(()->machine.getPnpJobProcessor().next());
                    synchronized(this) {event("native_step_complete",map("operation_id",id,"job",jobCounts()));operations.get(id).put("native_effect_pending",false);if(System.nanoTime()-lastSnapshotNanos>=100_000_000L)refresh();}
                    if(!more) {machine.getMotionPlanner().waitForCompletion(null,CompletionType.WaitForStillstand);synchronized(this){boardLoads.checkpoint(job);setJobState("completed");refresh();finish(id,"succeeded",jobSummary());} return null;}
                    if(singleStep){singleStepComplete=true;break;}
                }
                boolean cleanupRequested; synchronized(this){cleanupRequested=abortRequested;}
                if(!cleanupRequested){
                    machine.getMotionPlanner().waitForCompletion(null,CompletionType.WaitForStillstand);
                    synchronized(this){
                        // An abort may arrive while the completion barrier waits.
                        // Publish paused under the same lock the abort handler uses,
                        // or perform its cleanup below; never strand an accepted abort.
                        if(!abortRequested){
                            boardLoads.checkpoint(job);setJobState("paused");refresh();Map<String,Object> summary=jobSummary();
                            summary.put("pause_reason",singleStepComplete?"single-native-step-completed":"cooperative-pause");
                            summary.put("native_step_boundary",map("single_step_requested",singleStep,"single_step_completed",singleStepComplete,
                                "native_steps_started",operations.get(id).getOrDefault("native_steps_started",0),"standstill_confirmed",true,
                                "boundary_semantics","one-native-processor-next-call","physical_qualification",false));
                            transition(id,"paused",summary);return null;
                        }
                    }
                }
                    long started=((Number)operation(id).getOrDefault("native_steps_started",0)).longValue();
                    event("cleanup_intent",map("operation_id",id,"native_steps_started",started));
                    synchronized(this){operations.get(id).put("native_effect_pending",true);operations.get(id).put("native_effect_kind","native-job-cleanup");}
                    // Upstream abort cleanup assumes preflight has initialized its head. Before
                    // the first next(), no native job effect exists and cleanup is unnecessary.
                    if(started>0){
                        if(nativeLedger!=null){
                            try(NativeActionLedger.StepScope scope=nativeLedger.openAbortScope()){nativeAction(()->{machine.getPnpJobProcessor().abort();return null;});scope.complete();}
                            finally{lastNativeLedger=nativeLedger.snapshot();}
                        }else nativeAction(()->{machine.getPnpJobProcessor().abort();return null;});
                    }
                    event("cleanup_complete",map("operation_id",id,"native_cleanup_invoked",started>0,"reason",started>0?"native-abort-returned":"no-native-job-step-started"));
                    machine.getMotionPlanner().waitForCompletion(null,CompletionType.WaitForStillstand);
                    synchronized(this){boardLoads.checkpoint(job);operations.get(id).put("native_effect_pending",false);setJobState("aborted");refresh();finish(id,"aborted",jobSummary());}
            } catch(org.openpnp.machine.reference.VacuumSensing.ObserverFailure e) {
                settleUncertainFailure(id,"VACUUM_OUTCOME_UNKNOWN",e);
            } catch(NativeVacuumJournal.Fence e) {
                settleUncertainFailure(id,e.code,e);
            } catch(NativeActionLedger.DurabilityFence e) {
                if(nativeLedger!=null)lastNativeLedger=nativeLedger.snapshot();settleUncertainFailure(id,"NATIVE_ACTION_DURABILITY_UNKNOWN",e);
            } catch(NativeActionLedger.UnresolvedActionFence e) {
                if(nativeLedger!=null)lastNativeLedger=nativeLedger.snapshot();settleUncertainFailure(id,"NATIVE_ACTION_OUTCOME_UNKNOWN",e);
            } catch(GuiOwnership.ScriptRejected e) {
                settleUncertainFailure(id,"SCRIPT_POLICY_REJECTED",e);
            } catch(Exception e) {
                if(!nativeWorkDispatched && e instanceof Fault && "OWNERSHIP_REVOKED".equals(((Fault)e).code)
                    && preservePausedBeforeDispatch(id,"ownership-revoked-before-dispatch",e))return null;
                if(nativeLedger!=null){lastNativeLedger=nativeLedger.snapshot();}
                if(Boolean.TRUE.equals(lastNativeLedger.get("durability_fault"))||Boolean.TRUE.equals(lastNativeLedger.get("unresolved_action_fault")))settleUncertainFailure(id,"NATIVE_ACTION_OUTCOME_UNKNOWN",e);
                else{synchronized(this){setJobState("failed");}settleFailure(id,e);}
            }
            finally{if(sensingScope!=null)sensingScope.close();nativeOperationContext.remove();}
            return null;
        },true); }catch(Exception|Error admissionFailure){synchronized(this){
            if(pendingSubmission!=null&&pendingSubmission.entered)return;
            if(!(admissionFailure instanceof Exception)||!preservePausedBeforeDispatch(id,"native-admission-rejected",(Exception)admissionFailure)){
                finish(id,"failed",map("code","NATIVE_ADMISSION_REJECTED","message",safeMessage(admissionFailure),"native_effect_started",false));setJobState("failed");
            }
        }}
    }

    private synchronized boolean preservePausedBeforeDispatch(String id,String reason,Exception error)throws Exception {
        Map<String,Object> op=operations.get(id);
        if(!Objects.equals(activeOperation,id)||job==null||op==null||!Boolean.TRUE.equals(op.get("initialized"))
            ||Boolean.TRUE.equals(op.get("native_effect_pending"))||journalFault)return false;
        NativeSubmission submission=submissionContext.get();Map<String,Object> summary;
        if(submission!=null){setJobState("paused");refresh();summary=jobSummary();}
        else{
            // Native admission never entered. Reuse the last body-captured pause DTO;
            // this caller may be the HTTP/watchdog thread and cannot read the model.
            if(!(op.get("result") instanceof Map)||!"paused".equals(((Map<?,?>)op.get("result")).get("state")))return false;
            summary=freezeDto((Map<String,Object>)op.get("result"));
        }
        pauseRequested=true;
        summary.put("pause_reason",reason);summary.put("dispatch_error",safeMessage(error));summary.put("native_step_dispatched",false);
        summary.put("requires_new_control_session",session==null||System.nanoTime()>=sessionDeadline);summary.put("pending_abort",abortRequested);
        transition(id,"paused",summary);if(submission==null)jobState="paused";return true;
    }

    private synchronized void beginNativeEffect(String kind) throws Exception {
        if(!kind.equals("machine-disable"))requireVacuumNoFault();
        if(Set.of("motion","home","feed","actuator","nozzle-tip-change","nozzle-tip-calibration").contains(kind))invalidateVacuumReadiness("manual-effect");
        String id=nativeOperationContext.get();if(id==null)throw new IllegalStateException("Native effect has no operation context");
        event("native_effect_intent",map("operation_id",id,"kind",kind,"physical_outcome_verified",false));
        operations.get(id).put("native_effect_pending",true);operations.get(id).put("native_effect_kind",kind);
    }
    private synchronized void completeNativeEffect() throws Exception {
        String id=nativeOperationContext.get();Map<String,Object> op=operations.get(id);
        event("native_effect_outcome",map("operation_id",id,"kind",op.get("native_effect_kind"),"native_call_returned",true,"physical_outcome_verified",false));op.put("native_effect_pending",false);
    }

    private void settleUncertainFailure(String id,String code,Throwable failure) {
        Exception barrierFailure=null;
        try{machine.getMotionPlanner().waitForCompletion(null,CompletionType.WaitForStillstand);}catch(Exception e){barrierFailure=e;}
        synchronized(this){pauseRequested=true;setJobState(job==null?"absent":"outcome_unknown");try{
            refresh();finish(id,"outcome_unknown",map("code",code,"message",failure.getMessage(),"repeat_action_performed",false,
                "completion",map("standstill_confirmed",barrierFailure==null,"barrier_error",barrierFailure==null?null:safeMessage(barrierFailure))));
        }catch(Exception appendFailure){journalFault=true;}}
    }

    private boolean materialProfileAvailable() {
        return "sustained-workload".equals(simulatorProfile)&&guiOwnership==null
            ||Boolean.TRUE.equals(vacuumSources.get("available"))
                &&("vacuum-sensing".equals(simulatorProfile)||guiOwnership!=null&&guiOwnership.supportsSensingReconciliation());
    }
    private void requireSensingReconciliationProfile()throws Exception {
        requireSensingReconciliationProfile(false);
    }
    private void requireSensingReconciliationProfile(boolean restart)throws Exception {
        if(guiOwnership==null||!guiOwnership.supportsSensingReconciliation())throw new Fault("SENSING_RECOVERY_GUI_REQUIRED","Use the local GUI simulator recovery form");
        NativeVacuumSources.requireBoundedMachine(config);NativeVacuumSources.requireLifecycleSafe(config);
        if(restart){
            if(!guiOwnership.supportsSensingRestart())throw new Fault("SENSING_RESTART_GUI_REQUIRED","Use the explicit local simulator restart attachment");
            if(Boolean.TRUE.equals(NativeVacuumSources.reconciliationSnapshot(config).get("available")))throw new Fault("SENSING_RESTART_SOURCE_PRESENT","Restart requires an unattached current source");
            if(job!=null||!"absent".equals(jobState))throw new Fault("SENSING_RESTART_JOB_PRESENT","Restart requires an inactive host with no installed job");
            return;
        }
        if(!Boolean.TRUE.equals(NativeVacuumSources.reconciliationSnapshot(config).get("available")))throw new Fault("SENSING_RECOVERY_SOURCE_REQUIRED","A current owned simulator source is required");
    }
    private void requireSensingRecoveryIdle(boolean replaceJob)throws Exception {
        requireSensingRecoveryIdle(replaceJob,null);
    }
    private void requireSensingRecoveryIdle(boolean replaceJob,String continuationAttempt)throws Exception {
        requireNoPendingSubmission();if(activeOperation!=null||machine.isBusy())throw new Fault("BUSY","Wait for native completion before recovery");
        Set<String> continuedOperations=continuationAttempt==null?Set.of():faultedJobReplacement.continuationOperationIds(continuationAttempt);
        for(Map<String,Object> op:operations.values())if("outcome_unknown".equals(op.get("state"))&&!sensingReconciliation.liveDisposesOperation((String)op.get("operation_id"),instance)&&!isVacuumCommand((String)op.get("method"))&&!"local_native_sensing_reconciliation".equals(op.get("method"))
                &&!(replaceJob&&Set.of("openpnp_start_job","openpnp_step_job").contains(op.get("method"))&&(Objects.equals(op.get("job_id"),jobId)||continuedOperations.contains(op.get("operation_id")))))throw new Fault("RECOVERY_REQUIRED","An independent unknown operation requires separate reconciliation");
        if(machine.isEnabled())throw new Fault("SENSING_RECOVERY_DISABLED_REQUIRED","Disable before requesting local recovery");
    }
    private List<NativeVacuumSensing.Plan> sensingRecoveryPlans()throws Exception {
        NativeVacuumSources.requireBoundedMachine(config);List<NativeVacuumSensing.Plan> plans=new ArrayList<>();
        Map<String,Object> source=NativeSensingReconciliation.object(NativeVacuumSources.reconciliationSnapshot(config),"source");
        for(Head head:machine.getHeads())for(Nozzle nozzle:head.getNozzles()){
            NativeVacuumSensing.Plan plan=NativeVacuumSensing.admit(config,nozzle.getId());
            if(!source.equals(plan.source()))throw new Fault("SENSING_SOURCE_CHANGED","Recovery must include the complete shared source");plans.add(plan);
        }
        plans.sort(Comparator.comparing(plan->plan.nozzle().getId()));if(plans.isEmpty())throw new Fault("SENSING_BINDING_REQUIRED","No supported nozzle scope");return plans;
    }
    private List<Object> sensingRecoveryBindings(List<NativeVacuumSensing.Plan> plans)throws Exception {
        List<Object> result=new ArrayList<>();for(NativeVacuumSensing.Plan plan:plans)result.add(NativeVacuumJournal.binding(machineId,instance,revisionString(),plan.nozzle().getId(),plan.nozzle().getNozzleTip().getId(),plan.nozzle().getVacuumSenseActuator().getId(),plan.source()));return result;
    }
    private synchronized Object captureSensingReconciliation(JsonObject p)throws Exception {
        boolean restart=NativeSensingReconciliation.RESTART_KIND.equals(text(p,"recovery_kind"));
        requireSensingReconciliationProfile(restart);fence(epoch);
        if(!machine.isTask(Thread.currentThread())||machine.isEnabled())throw new Fault("SENSING_RECOVERY_EXECUTOR","Capture requires the disabled owning native executor");
        if(restart)return captureSensingRestart(p);
        boolean replacement="replace-faulted-job-attempt".equals(text(p,"recovery_kind"));
        boolean continuation=NativeSensingReconciliation.CONTINUATION_KIND.equals(text(p,"recovery_kind"));
        if(!replacement&&!continuation&&(job!=null||!"absent".equals(jobState)))throw new Fault("SENSING_RECOVERY_JOB_DEPENDENCIES","Use job/material/board reconciliation for an existing job");
        List<NativeVacuumSensing.Plan> plans=sensingRecoveryPlans();Map<String,Object> source=NativeVacuumSources.reconciliationSnapshot(config);
        if(continuation)return captureSensingReplacementContinuation(p,plans,source);
        if(replacement)return captureFaultedSensingJob(p,plans,source);
        for(Object raw:NativeSensingReconciliation.list(source,"nozzles",64)){
            Map<String,Object> signal=NativeSensingReconciliation.object(NativeSensingReconciliation.asObject(raw),"signal");
            if(Boolean.TRUE.equals(signal.get("retained_observed"))||Boolean.TRUE.equals(signal.get("lost_observed"))||signal.get("active_native_check_id")!=null)throw new Fault("SENSING_RECOVERY_MATERIAL_DEPENDENCIES","Pending or retained material requires native disposal reconciliation");
        }
        for(NativeVacuumSensing.Plan plan:plans)if(plan.nozzle().getPart()!=null)throw new Fault("SENSING_RECOVERY_MATERIAL_DEPENDENCIES","Native held material requires disposal reconciliation");
        Map<String,Object> dependencies=map("action_ids",List.of(),"operation_ids",standaloneRecoveryDependencies(),"board_load_ids",List.of(),"material_load_ids",List.of(),"job_attempt_ids",List.of(),"unresolved_dependencies",List.of());
        NativeSensingReconciliation.Capture capture=vacuumJournal.captureFaultSet(map("machine_id",machineId,"bridge_instance_id",instance,"config_revision",revisionString(),"native_graph_sha256",sha256(VACUUM_JSON.toJson(NativeSettings.describe(config))),"nozzle_bindings",sensingRecoveryBindings(plans),"job_context",null,"dependencies",dependencies));
        validateStandaloneSensingFaults(capture.payload);
        String id=UUID.randomUUID().toString(),requestOperation=nativeOperationContext.get();Instant expiry=Instant.now().plusSeconds(300);
        Map<String,Object> record=sensingReconciliation.taskRecord(id,requestOperation,text(p,"request_id"),capture,text(p,"recovery_kind"),epoch,expiry.toString());
        event("sensing_reconciliation_task",record);
        Map<String,Object> task=map("task_id",id,"recovery_kind",text(p,"recovery_kind"),"expires_at",expiry.toString(),"expires_in_ms",300000,"fault_set_sha256",capture.digest,
            "snapshot",map("context",record,"faults",capture.payload.get("faults"),"proposed_effects",List.of("replace declared synthetic sensor source","enable and home native simulator","move all affected nozzles to Safe Z","pulse vacuum and verify each nozzle is empty","disable simulator","preserve original failed or unknown outcomes")));
        pendingSensingReconciliation=new PendingSensingReconciliation(id,requestOperation,epoch,capture,task,plans);
        return map("task_id",id,"profile",NativeSensingReconciliation.PROFILE,"state","awaiting_local_action","expires_at",expiry.toString(),"fault_set_sha256",capture.digest,"simulation_only",true,"hardware_qualified",false);
    }
    private Object captureFaultedSensingJob(JsonObject p,List<NativeVacuumSensing.Plan> plans,Map<String,Object> source)throws Exception {
        requireJobEditScope(p);materialLoads.requireRevision(text(p,"expected_material_revision"));
        if(job==null||!Set.of("failed","outcome_unknown").contains(jobState))throw new Fault("SENSING_RECOVERY_JOB_REQUIRED","Select the exact completed faulted native job");
        String original=text(p,"original_operation_id");requireTerminalSensingJob(original);
        NativeFaultedJobReplacement.Capture replacement=faultedJobReplacement.capture(job,jobId,original);
        Map<String,Object> admitted=operations.get(original),lineage=NativeSensingReconciliation.object(admitted,"job_lineage");
        Map<String,Object> jobContext=map("job_id",jobId,"job_revision",boardLoads.jobRevision(),"board_load_revision",boardLoads.revision(),"material_setup_revision",materialLoads.revision(),"lineage_id",lineage.get("lineage_id"),"lineage_revision",lineage.get("lineage_revision"));
        NativeSensingReconciliation.Capture capture=vacuumJournal.captureFaultSet(map("machine_id",machineId,"bridge_instance_id",instance,"config_revision",revisionString(),"native_graph_sha256",sha256(VACUUM_JSON.toJson(NativeSettings.describe(config))),"nozzle_bindings",sensingRecoveryBindings(plans),"job_context",jobContext,"dependencies",replacement.dependencies()));
        validateJobSensingFaults(capture.payload);requireTerminalSourceObservations(source,replacement.dependencies());
        String id=UUID.randomUUID().toString(),requestOperation=nativeOperationContext.get();Instant expiry=Instant.now().plusSeconds(300);
        Map<String,Object> record=sensingReconciliation.taskRecord(id,requestOperation,text(p,"request_id"),capture,"replace-faulted-job-attempt",epoch,expiry.toString());
        event("sensing_reconciliation_task",record);
        Map<String,Object> task=map("task_id",id,"recovery_kind","replace-faulted-job-attempt","expires_at",expiry.toString(),"expires_in_ms",300000,"fault_set_sha256",capture.digest,
            "snapshot",map("context",record,"faults",capture.payload.get("faults"),"source",source,"replacement",replacement.payload,"proposed_effects",List.of(
                "retire only captured sensing calls whose native tasks have ended","replace declared synthetic sensor source","enable and home the native simulator",
                "move every affected nozzle through the native discard path and release","probe every nozzle with its final source generation","disable simulator",
                "retire captured trays and bind fresh full simulator trays","quarantine the old board attempt and bind distinct replacement boards",
                "retain every old operation, feed and placed outcome","require fresh registration and validation before running the replacement")));
        PendingSensingReconciliation pending=new PendingSensingReconciliation(id,requestOperation,epoch,capture,task,plans);
        pending.replacementCapture=replacement;pending.capturedSource=NativeSensingReconciliation.freeze(source);pending.originalJobOperation=original;pendingSensingReconciliation=pending;
        return map("task_id",id,"profile",NativeSensingReconciliation.PROFILE,"state","awaiting_local_action","expires_at",expiry.toString(),"fault_set_sha256",capture.digest,"simulation_only",true,"hardware_qualified",false);
    }
    private Object captureSensingRestart(JsonObject p)throws Exception {
        String attempt=text(p,"replacement_attempt_id");
        NativeFaultedJobReplacement.RestartStage stage=faultedJobReplacement.stageRestart(attempt);
        try{
            NativeFaultedJobReplacement.RestartCapture restart=faultedJobReplacement.captureRestart(stage);
            Map<String,Object> original=NativeSensingReconciliation.asObject(NativeSensingReconciliation.object(faultedJobReplacement.status(),"intents").get(attempt));
            Map<String,Object> originalTask=NativeSensingReconciliation.object(sensingReconciliation.snapshot((String)original.get("task_id")),"task");
            Map<String,Map<String,Object>> historical=NativeSensingReconciliation.bindingByNozzle(NativeSensingReconciliation.list(NativeSensingReconciliation.object(originalTask,"fault_set"),"nozzle_bindings",64));
            List<Object> bindings=new ArrayList<>();Set<String> seen=new HashSet<>();
            for(Head head:machine.getHeads())for(Nozzle nozzle:head.getNozzles()){
                if(!(nozzle instanceof ReferenceNozzle))throw new Fault("SENSING_RESTART_BINDING_CHANGED","Restart requires a supported native sensing nozzle");
                ReferenceNozzle sensingNozzle=(ReferenceNozzle)nozzle;
                Map<String,Object> old=historical.get(nozzle.getId());
                if(old==null||nozzle.getNozzleTip()==null||sensingNozzle.getVacuumSenseActuator()==null
                    ||!Objects.equals(old.get("nozzle_tip_id"),nozzle.getNozzleTip().getId())||!Objects.equals(old.get("sensor_id"),sensingNozzle.getVacuumSenseActuator().getId()))
                    throw new Fault("SENSING_RESTART_BINDING_CHANGED","Current nozzle topology differs from the exact historical recovery scope");
                bindings.add(NativeVacuumJournal.binding(machineId,instance,revisionString(),nozzle.getId(),nozzle.getNozzleTip().getId(),sensingNozzle.getVacuumSenseActuator().getId(),NativeSensingReconciliation.object(old,"source")));seen.add(nozzle.getId());
            }
            if(!seen.equals(historical.keySet()))throw new Fault("SENSING_RESTART_BINDING_CHANGED","Restart must include every original nozzle");
            NativeSensingReconciliation.Capture capture=vacuumJournal.captureFaultSet(map("machine_id",machineId,"bridge_instance_id",instance,"config_revision",revisionString(),"native_graph_sha256",sha256(VACUUM_JSON.toJson(NativeSettings.describe(config))),"nozzle_bindings",bindings,"job_context",restart.jobContext(),"dependencies",restart.dependencies()));
            validateJobSensingFaults(capture.payload,attempt,true,false);
            String id=UUID.randomUUID().toString(),requestOperation=nativeOperationContext.get();Instant expiry=Instant.now().plusSeconds(300);
            Map<String,Object> record=sensingReconciliation.taskRecordForRestart(id,requestOperation,text(p,"request_id"),capture,restart,epoch,expiry.toString());
            event("sensing_reconciliation_task",record);
            Map<String,Object> task=map("task_id",id,"recovery_kind",NativeSensingReconciliation.RESTART_KIND,"expires_at",expiry.toString(),"expires_in_ms",300000,"fault_set_sha256",capture.digest,
                "snapshot",map("context",record,"faults",capture.payload.get("faults"),"replacement",restart.payload,"source",map("available",false,"binding_scope","historical-provenance-only","current_source_created",false),"source_binding_scope","historical-provenance-only; current source absent",
                    "proposed_effects",List.of("record exclusive current journal ownership while preserving prior callback outcomes","reattach the exact reconstructed inactive job and replacement candidate",
                        "create a fresh simulator source with all nozzle material initially unknown","observe current native tray counters and complete board history without confirming physical loads",
                        "require a separate local continuation, source disposal and fresh validation before execution")));
            PendingSensingReconciliation pending=new PendingSensingReconciliation(id,requestOperation,epoch,capture,task,List.of());pending.restartStage=stage;pending.restartCapture=restart;pendingSensingReconciliation=pending;
            return map("task_id",id,"profile",NativeSensingReconciliation.PROFILE,"state","awaiting_local_action","expires_at",expiry.toString(),"fault_set_sha256",capture.digest,"replacement_attempt_id",attempt,"execution_authority_restored",false,"simulation_only",true,"hardware_qualified",false);
        }catch(Exception|Error failure){stage.close();throw failure;}
    }
    private Object captureSensingReplacementContinuation(JsonObject p,List<NativeVacuumSensing.Plan> plans,Map<String,Object> source)throws Exception {
        String attempt=text(p,"replacement_attempt_id");NativeFaultedJobReplacement.ContinuationCapture replacement=faultedJobReplacement.captureContinuationForHost(attempt);
        NativeSensingReconciliation.Capture capture=vacuumJournal.captureFaultSet(map("machine_id",machineId,"bridge_instance_id",instance,"config_revision",revisionString(),"native_graph_sha256",sha256(VACUUM_JSON.toJson(NativeSettings.describe(config))),"nozzle_bindings",sensingRecoveryBindings(plans),"job_context",replacement.jobContext(),"dependencies",replacement.dependencies()));
        validateJobSensingFaults(capture.payload,attempt,false,false);requireTerminalSourceObservations(source,replacement.dependencies());
        String id=UUID.randomUUID().toString(),requestOperation=nativeOperationContext.get();Instant expiry=Instant.now().plusSeconds(300);
        Map<String,Object> record=sensingReconciliation.taskRecordForContinuation(id,requestOperation,text(p,"request_id"),capture,replacement,epoch,expiry.toString());
        event("sensing_reconciliation_task",record);
        Map<String,Object> task=map("task_id",id,"recovery_kind",NativeSensingReconciliation.CONTINUATION_KIND,"expires_at",expiry.toString(),"expires_in_ms",300000,"fault_set_sha256",capture.digest,
            "snapshot",map("context",record,"faults",capture.payload.get("faults"),"source",source,"replacement",replacement.payload,"proposed_effects",List.of(
                "retain every original failed or unknown operation and completed replacement step","repair the captured synthetic source and retire only ended sensing calls",
                "dispose only unresolved current nozzle material through the native discard path","perform fresh native empty-nozzle probes and disable the simulator",
                "adopt completed lineage and load steps without repeating their effects","replace untouched loads and explicitly supersede uncertain loads with new identities",
                "publish the exact retained candidate under a new publication receipt","require fresh registration and validation before running this replacement")));
        PendingSensingReconciliation pending=new PendingSensingReconciliation(id,requestOperation,epoch,capture,task,plans);pending.continuationCapture=replacement;pending.capturedSource=NativeSensingReconciliation.freeze(source);pendingSensingReconciliation=pending;
        return map("task_id",id,"profile",NativeSensingReconciliation.PROFILE,"state","awaiting_local_action","expires_at",expiry.toString(),"fault_set_sha256",capture.digest,"replacement_attempt_id",attempt,"simulation_only",true,"hardware_qualified",false);
    }
    private void requireTerminalSensingJob(String id)throws Exception {
        Map<String,Object> op=operations.get(id);
        if(op==null||!Set.of("openpnp_start_job","openpnp_step_job").contains(op.get("method"))||!Set.of("failed","outcome_unknown").contains(op.get("state"))||!Objects.equals(jobId,op.get("job_id")))throw new Fault("SENSING_RECOVERY_JOB_REQUIRED","Original operation must be the exact faulted native job");
        if(!Boolean.TRUE.equals(NativeSensingReconciliation.object(op,"native_completion").get("native_wrapper_completed")))throw new Fault("SENSING_RECOVERY_NATIVE_PENDING","The original native task wrapper has not completed");
        for(String key:List.of("job_revision","board_load_revision","material_setup_revision")){
            Object current=key.equals("job_revision")?boardLoads.jobRevision():key.equals("board_load_revision")?boardLoads.revision():materialLoads.revision();
            if(!Objects.equals(current,op.get(key)))throw new Fault("SENSING_RECOVERY_JOB_CHANGED","Faulted job setup has changed: "+key);
        }
    }
    private void validateJobSensingFaults(Map<String,Object> capture)throws IOException {
        validateJobSensingFaults(capture,null,false,false);
    }
    private void validateJobSensingFaults(Map<String,Object> capture,String attempt,boolean stagingRestart,boolean replay)throws IOException {
        Map<String,Object> jobContext=NativeSensingReconciliation.object(capture,"job_context"),dependencies=NativeSensingReconciliation.object(capture,"dependencies");
        Set<String> included=new HashSet<>(NativeSensingReconciliation.ids(dependencies,"operation_ids",256,false));
        for(Object raw:NativeSensingReconciliation.list(capture,"faults",1024)){
            Map<String,Object> fault=NativeSensingReconciliation.asObject(raw),op=operations.get(fault.get("operation_id"));
            if(op==null||!Set.of("failed","outcome_unknown").contains(op.get("state")))throw new IOException("Job fault has no exact failed operation");
            Map<String,Object> context=NativeSensingReconciliation.object(NativeSensingReconciliation.object(fault,"payload"),"context");
            if("recovery".equals(context.get("scope"))){
                boolean wrapper=op.get("native_completion") instanceof Map&&Boolean.TRUE.equals(NativeSensingReconciliation.object(op,"native_completion").get("native_wrapper_completed"));
                boolean priorProcess=attempt!=null&&!instance.equals(op.get("bridge_instance_id"))&&(stagingRestart||priorProcessDispositionMatches(attempt,(String)op.get("operation_id"),replay));
                if(!included.contains(op.get("operation_id"))||!"local_native_sensing_reconciliation".equals(op.get("method"))||!wrapper&&!priorProcess)throw new IOException("Prior job recovery lacks its exact completed wrapper or scoped prior-process disposition");
                Map<String,Object> recovery=NativeSensingReconciliation.object(context,"recovery_context"),prior=NativeSensingReconciliation.object(sensingReconciliation.snapshot((String)op.get("task_id")),"task"),priorScope=NativeSensingReconciliation.object(prior,"fault_set");
                if(!Set.of("replace-faulted-job-attempt",NativeSensingReconciliation.CONTINUATION_KIND,NativeSensingReconciliation.RESTART_KIND).contains(prior.get("recovery_kind"))||!Objects.equals(context.get("request_id"),op.get("reconciliation_request_id"))||!Objects.equals(op.get("task_id"),recovery.get("task_id"))||!Objects.equals(prior.get("fault_set_sha256"),recovery.get("fault_set_sha256"))||!Objects.equals(context.get("job_context"),priorScope.get("job_context"))||!Objects.equals(NativeSensingReconciliation.object(priorScope,"job_context").get("job_id"),jobContext.get("job_id")))throw new IOException("Prior recovery sensing fault has a different task or original job");
            }else if("manual".equals(context.get("scope"))){
                if(!Objects.equals(context.get("request_id"),op.get("request_id")))throw new IOException("Job fault request differs");
                if(!isVacuumCommand((String)op.get("method"))||context.get("job_context")!=null||!Objects.equals(op.get("vacuum_nozzle_id"),context.get("nozzle_id")))throw new IOException("Unrelated manual sensing fault");
            }else{
                if(!Objects.equals(context.get("request_id"),op.get("request_id")))throw new IOException("Job fault request differs");
                if(!included.contains(op.get("operation_id"))||!Set.of("openpnp_start_job","openpnp_step_job").contains(op.get("method")))throw new IOException("Faulted job dependency omitted");
                Map<String,Object> actual=NativeSensingReconciliation.object(context,"job_context");
                if(!Objects.equals(actual.get("job_id"),jobContext.get("job_id")))throw new IOException("Sensing fault belongs to another job");
                for(String key:List.of("job_id","job_revision","board_load_revision","material_setup_revision"))if(!Objects.equals(actual.get(key),op.get(key)))throw new IOException("Faulted job context differs");
            }
        }
    }
    private void requireTerminalSourceObservations(Map<String,Object> source,Map<String,Object> dependencies)throws Exception {
        Set<String> included=new HashSet<>(NativeSensingReconciliation.ids(dependencies,"operation_ids",256,false));
        Set<String> pending=new TreeSet<>();
        for(Object row:NativeSensingReconciliation.list(source,"nozzles",64)){
            Map<String,Object> signal=NativeSensingReconciliation.object(NativeSensingReconciliation.asObject(row),"signal");
            for(Object raw:NativeSensingReconciliation.list(signal,"pending_observations",256)){
                Map<String,Object> observation=NativeSensingReconciliation.asObject(raw),context=NativeSensingReconciliation.object(observation,"bridge_context");
                String operation=(String)context.get("operation_id");Map<String,Object> op=operations.get(operation);
                if(!included.contains(operation)||op==null||!instance.equals(op.get("bridge_instance_id"))||!Set.of("failed","outcome_unknown","succeeded").contains(op.get("state"))
                    ||!Boolean.TRUE.equals(NativeSensingReconciliation.object(op,"native_completion").get("native_wrapper_completed")))throw new Fault("SENSING_RECOVERY_NATIVE_PENDING","A captured sensing call has no terminal native wrapper in this process");
                pending.add((String)observation.get("observation_id"));
            }
        }
        if(!pending.equals(new TreeSet<>(NativeSensingReconciliation.ids(source,"pending_observation_ids",256,false))))throw new Fault("SENSING_RECOVERY_SOURCE_CHANGED","Incomplete pending observation union");
    }
    private void validateStandaloneSensingFaults(Map<String,Object> capture)throws IOException {
        Map<String,Object> dependencies=NativeSensingReconciliation.object(capture,"dependencies");
        if(capture.get("job_context")!=null||NativeSensingReconciliation.hasWorkpieceDependencies(dependencies))throw new IOException("Standalone recovery has external dependencies");
        Set<String> prior=new HashSet<>(NativeSensingReconciliation.ids(dependencies,"operation_ids",256,false));
        for(String id:prior)requireTerminalStandaloneRecovery(id);
        for(Object raw:NativeSensingReconciliation.list(capture,"faults",1024)){
            Map<String,Object> fault=NativeSensingReconciliation.asObject(raw),op=operations.get(fault.get("operation_id"));
            if(op==null||!Set.of("failed","outcome_unknown").contains(op.get("state")))throw new IOException("Standalone fault lacks an exact failed sensing operation");
            Map<String,Object> payload=NativeSensingReconciliation.object(fault,"payload"),context=NativeSensingReconciliation.object(payload,"context");
            if(context.get("job_context")!=null)throw new IOException("Standalone fault has job dependencies");
            if("manual".equals(context.get("scope"))){
                if(!isVacuumCommand((String)op.get("method"))||!Objects.equals(op.get("request_id"),context.get("request_id"))||!Objects.equals(op.get("vacuum_nozzle_id"),context.get("nozzle_id")))throw new IOException("Standalone fault has nonmanual effects");
            }else if("recovery".equals(context.get("scope"))){
                String id=(String)op.get("operation_id");requireTerminalStandaloneRecovery(id);
                if(!prior.contains(id)||!Objects.equals(op.get("reconciliation_request_id"),context.get("request_id"))||!Objects.equals(op.get("task_id"),NativeSensingReconciliation.object(context,"recovery_context").get("task_id")))throw new IOException("Prior recovery fault omitted from exact dependency scope");
            }else throw new IOException("Standalone fault has another execution scope");
        }
    }
    private List<String> standaloneRecoveryDependencies()throws IOException {
        TreeSet<String> ids=new TreeSet<>();
        for(Map<String,Object> row:sensingReconciliation.summaries()){
            String id=(String)row.get("recovery_operation_id");if(id==null||sensingReconciliation.liveDisposesOperation(id,instance))continue;
            if("reconciliation_unknown".equals(row.get("state"))){requireTerminalStandaloneRecovery(id);ids.add(id);}
        }
        return List.copyOf(ids);
    }
    private void requireTerminalStandaloneRecovery(String id)throws IOException {
        Map<String,Object> op=operations.get(id);
        if(op==null||!"local_native_sensing_reconciliation".equals(op.get("method"))||!Set.of("failed","outcome_unknown","succeeded").contains(op.get("state")))throw new IOException("Prior recovery operation is not terminal");
        Map<String,Object> nativeCompletion=NativeSensingReconciliation.object(op,"native_completion");
        if(!Boolean.TRUE.equals(nativeCompletion.get("native_wrapper_completed")))throw new IOException("Prior native recovery wrapper remains unresolved");
        Map<String,Object> prior=sensingReconciliation.snapshot((String)op.get("task_id")),record=NativeSensingReconciliation.object(prior,"task"),scope=NativeSensingReconciliation.object(record,"fault_set");
        if(!"reconciliation_unknown".equals(prior.get("state"))||!"restore-sensing-readiness".equals(record.get("recovery_kind"))||!machineId.equals(scope.get("machine_id"))||scope.get("job_context")!=null||NativeSensingReconciliation.hasWorkpieceDependencies(NativeSensingReconciliation.object(scope,"dependencies")))throw new IOException("Prior recovery has independent workpiece dependencies");
    }
    private boolean sensingDispositionsVerified(Map<String,Object> capture,Map<String,Object> dispositions,boolean replay)throws IOException {
        // Job recovery requires independently forced material and board subreceipts before this can accept it.
        if(capture.get("job_context")!=null){validateJobSensingFaults(capture,(String)dispositions.get("replacement_attempt_id"),false,true);return faultedJobReplacement.verified(capture,dispositions,replay);}validateStandaloneSensingFaults(capture);
        return "none-present".equals(dispositions.get("nozzle_material"))&&NativeSensingReconciliation.list(dispositions,"feeder_loads",64).isEmpty()&&NativeSensingReconciliation.list(dispositions,"board_loads",64).isEmpty();
    }
    private boolean priorProcessDispositionMatches(String attempt,String operation,boolean replay)throws IOException {
        try{return faultedJobReplacement.priorProcessDisposedForContinuation(attempt,operation,replay);}
        catch(Exception failure){if(failure instanceof IOException)throw(IOException)failure;throw new IOException("Prior-process disposition cannot be verified",failure);}
    }
    private synchronized boolean terminalReplacementRecoveryCompleted(String id)throws IOException {
        Map<String,Object> op=operations.get(id);if(op==null||!"local_native_sensing_reconciliation".equals(op.get("method"))||!Set.of("failed","outcome_unknown","succeeded").contains(op.get("state")))return false;
        Map<String,Object> completion=NativeSensingReconciliation.object(op,"native_completion");if(!Boolean.TRUE.equals(completion.get("native_wrapper_completed")))return false;
        if(!instance.equals(op.get("bridge_instance_id")))return true;
        NativeSubmission actual=sensingCompletedWrappers.get(id);return actual!=null&&actual.entered&&actual.exited&&actual.publicationAttempted&&actual.completionObservation!=null&&Boolean.TRUE.equals(actual.completionObservation.get("native_wrapper_completed"))&&actual.token.equals(completion.get("submission_id"));
    }
    private synchronized void requireRestartOwnership(String operation,String currentInstance,String currentRevision,Job original,String originalId,Map<String,Object> prior)throws Exception {
        if(closed||journalFault||journal==null||!journal.isOpen()||journalLock==null||!journalLock.isValid()
            ||!instance.equals(currentInstance)||!revisionString().equals(currentRevision)||Configuration.get()!=config||config.getMachine()!=machine
            ||!machine.isTask(Thread.currentThread())||machine.isEnabled()||!operation.equals(nativeOperationContext.get())||!operation.equals(activeOperation)
            ||pendingSubmission==null||!operation.equals(pendingSubmission.operationId)||!pendingSubmission.entered||pendingSubmission.exited)
            throw new Fault("SENSING_RESTART_OWNERSHIP_CHANGED","Restart requires exclusive current journal and native callback ownership");
        if(job!=null&&(job!=original||!Objects.equals(jobId,originalId)))throw new Fault("SENSING_RESTART_JOB_CHANGED","Another host job is installed");
        PendingSensingReconciliation task=pendingSensingReconciliation;
        if(task==null||task.restartCapture==null||!operation.equals(task.localOperation))throw new Fault("SENSING_RESTART_AUTHORITY_REQUIRED","No exact current local restart callback");
        requireLocalSensingRecovery(task,true);guiOwnership.requireNativeOwnership();
        for(Map.Entry<String,Object> entry:prior.entrySet()){
            Map<String,Object> old=operations.get(entry.getKey());
            // Ordinary operation records use GSON's historical null-omitting wire shape.
            // Compare that exact durable shape, not transient null-valued map entries.
            Map<String,Object> durable=old==null?null:NativeJournalJson.parseObject(GSON.toJson(plain(old)));
            if(old==null||instance.equals(old.get("bridge_instance_id"))||!NativeFaultedJobReplacement.same(durable,entry.getValue()))throw new Fault("SENSING_RESTART_PRIOR_CHANGED","Prior operation is outside the captured old-instance history");
        }
    }
    private boolean restartObservationsVerified(Map<String,Object> capture,Map<String,Object> context,Map<String,Object> observations,boolean replay)throws IOException {
        return faultedJobReplacement!=null&&faultedJobReplacement.verifiedRestartObservations(capture,context,observations,replay);
    }
    private boolean sensingWrapperCompleted(String id,String originalInstance,boolean replay)throws IOException {
        Map<String,Object> op=operations.get(id);if(op==null||!"local_native_sensing_reconciliation".equals(op.get("method"))||!"succeeded".equals(op.get("state"))||!originalInstance.equals(op.get("bridge_instance_id")))return false;
        Map<String,Object> completion=NativeSensingReconciliation.object(op,"native_completion");
        if(!Boolean.TRUE.equals(completion.get("native_wrapper_completed"))||!Boolean.TRUE.equals(completion.get("native_wrapper_succeeded")))return false;
        if(replay)return true;
        NativeSubmission actual=sensingCompletedWrappers.get(id);
        return originalInstance.equals(instance)&&actual!=null&&actual.entered&&actual.exited&&actual.publicationAttempted&&actual.completionObservation!=null&&Boolean.TRUE.equals(actual.completionObservation.get("native_wrapper_succeeded"))&&actual.token.equals(completion.get("submission_id"));
    }
    private void requireLocalSensingRecovery(PendingSensingReconciliation task,boolean submitted)throws Exception {
        if(task!=pendingSensingReconciliation||!task.active||task.used!=submitted||closed||journalFault||System.nanoTime()>=task.deadline||task.localAuthority==null||!task.localAuthority.getAsBoolean())throw new Fault("SENSING_RECOVERY_AUTHORITY_STALE","The one-use local recovery has expired or been revoked");
        fence(task.owner);guiOwnership.requireRemoteGrant();
        if(!Objects.equals(revisionString(),task.capture.payload.get("config_revision")))throw new Fault("REVISION_CONFLICT","Recovery configuration changed");
        for(NativeVacuumSensing.Plan plan:task.plans)plan.validateCurrentGraph();
        if(task.restartCapture!=null&&!Objects.equals(task.capture.payload.get("native_graph_sha256"),sha256(VACUUM_JSON.toJson(NativeSettings.describe(config)))))throw new Fault("SENSING_RESTART_GRAPH_CHANGED","Current restart graph differs from the local task");
    }
    private void sensingReconciliationMaintenance()throws Exception {
        PendingSensingReconciliation task=pendingSensingReconciliation;if(task==null)return;
        if(task.localOperation!=null){
            Map<String,Object> op=operations.get(task.localOperation);
            if(!task.completionDelivered&&(pendingSubmission==null||!task.localOperation.equals(pendingSubmission.operationId))&&op!=null&&Set.of("succeeded","failed","outcome_unknown").contains(op.get("state"))){
                if(!task.intentWritten&&!journalFault)event("sensing_reconciliation_closed",sensingReconciliation.closedRecord(task.id,"scope_stale"));
                if(!task.intentWritten){
                    if(task.restartAttestation!=null)task.restartAttestation.close();
                    if(task.restartStage!=null)task.restartStage.close();
                }
                task.completionDelivered=true;task.active=false;guiOwnership.dismissSensingReconciliation(task.id,"submission-finished");
                if(task.completion!=null){Map<String,Object> result=map("operation",operation(task.localOperation),"reconciliation",sensingReconciliation.snapshot(task.id));
                    if(task.resolved||task.restartCompleted)task.completion.complete(result);else task.completion.completeExceptionally(new Fault("SENSING_RECOVERY_UNRESOLVED","Recovery remains unresolved; inspect its operation and fault records"));}
            }return;
        }
        if(!task.active)return;
        String reason=closed||journalFault||session==null||epoch!=task.owner?"cancelled":System.nanoTime()>=task.deadline?"expired":!Objects.equals(revisionString(),task.capture.payload.get("config_revision"))?"scope_stale":null;
        if(reason!=null){closeSensingReconciliation(task,reason);return;}
        Map<String,Object> request=operations.get(task.requestOperation);
        if(request==null||Set.of("failed","outcome_unknown").contains(request.get("state"))){closeSensingReconciliation(task,"scope_stale");return;}
        if(!task.presented&&pendingSubmission==null&&"succeeded".equals(request.get("state"))){
            task.presented=true;guiOwnership.presentSensingReconciliation(freezeDto(task.task),new GuiOwnership.SensingReconciliationSubmission(){
                public CompletionStage<Map<String,Object>> submit(java.util.function.BooleanSupplier authority){
                    if(task.restartCapture!=null)return CompletableFuture.failedFuture(new Fault("SENSING_RESTART_ATTESTATION_REQUIRED","Use the local restart action with a fresh GUI attestation"));
                    return submitLocalSensingReconciliation(task,authority);
                }
                public CompletionStage<Map<String,Object>> submitRestart(GuiSensingFixture.RestartAttestation attestation,java.util.function.BooleanSupplier authority){
                    synchronized(Bridge.this){
                        if(task.restartCapture==null||attestation==null||task.restartAttestation!=null||task.queued.get()||task!=pendingSensingReconciliation||!task.active)
                            return CompletableFuture.failedFuture(new Fault("SENSING_RESTART_ATTESTATION_REQUIRED","An exact fresh one-use local restart attestation is required"));
                        task.restartAttestation=attestation;
                    }
                    return submitLocalSensingReconciliation(task,authority);
                }
                public void cancel(String reason){synchronized(Bridge.this){try{closeSensingReconciliation(task,"cancelled");}catch(Exception failure){journalFault=true;pauseRequested=true;}}}
            });
        }
    }
    private void closeSensingReconciliation(PendingSensingReconciliation task,String reason)throws Exception {
        if(task!=pendingSensingReconciliation||!task.active||task.used)return;task.active=false;
        guiOwnership.dismissSensingReconciliation(task.id,reason);if(!closed&&!journalFault)event("sensing_reconciliation_closed",sensingReconciliation.closedRecord(task.id,reason));
        if(task.restartAttestation!=null)task.restartAttestation.close();
        if(task.restartStage!=null)task.restartStage.close();
    }
    private CompletionStage<Map<String,Object>> submitLocalSensingReconciliation(PendingSensingReconciliation task,java.util.function.BooleanSupplier authority){
        CompletableFuture<Map<String,Object>> done=new CompletableFuture<>();if(!task.queued.compareAndSet(false,true)){done.completeExceptionally(new IllegalStateException("Local recovery is one-use"));return done;}
        try{httpThreads.execute(()->{try{synchronized(Bridge.this){
            task.localAuthority=authority;drainNativeCompletion();requireLocalSensingRecovery(task,false);requireSensingRecoveryIdle(task.replacementCapture!=null||task.continuationCapture!=null||task.restartCapture!=null,task.restartCapture!=null?(String)task.restartCapture.payload.get("replacement_attempt_id"):task.continuationCapture==null?null:(String)task.continuationCapture.payload.get("replacement_attempt_id"));requireRequestCapacity();vacuumJournal.validateCapture(task.capture.payload,null);
            task.used=true;task.completion=done;String id=UUID.randomUUID().toString(),request=UUID.randomUUID().toString();
            String originalRequest=(String)NativeSensingReconciliation.object(sensingReconciliation.snapshot(task.id),"task").get("request_id");task.localOperation=id;
            Map<String,Object> op=map("operation_id",id,"request_id",request,"reconciliation_request_id",originalRequest,"request_digest",task.capture.digest,"method","local_native_sensing_reconciliation","state","accepted","bridge_instance_id",instance,"config_revision",revisionString(),"accepted_at",Instant.now().toString(),"task_id",task.id);
            event("operation",op);operations.put(id,op);requests.put(request,id);requestDigests.put(request,task.capture.digest);activeOperation=id;
            try{submitNative(id,task.owner,false,false,()->{nativeOperationContext.set(id);
                try{nativeAction(()->{runSensingReconciliation(task);return null;});}
                catch(Exception|Error failure){synchronized(Bridge.this){
                    if(task.intentWritten&&!journalFault)markSensingReconciliationUnknown(task,"native_failure");
                    finish(id,task.intentWritten?"outcome_unknown":"failed",map("code","SENSING_RECOVERY_FAILED","message",safeMessage(failure),"repeat_action_performed",false));
                }}finally{nativeOperationContext.remove();}return null;
            },true);}catch(Exception admission){finish(id,"failed",map("code","NATIVE_ADMISSION_REJECTED","message",safeMessage(admission),"native_effect_started",false));}
        }}catch(Throwable failure){synchronized(Bridge.this){if(!task.used){try{closeSensingReconciliation(task,"scope_stale");}catch(Exception publication){journalFault=true;pauseRequested=true;failure.addSuppressed(publication);}}}done.completeExceptionally(failure);}});}catch(RejectedExecutionException failure){done.completeExceptionally(failure);}return done;
    }
    private void runSensingReconciliation(PendingSensingReconciliation task)throws Exception {
        synchronized(this){requireLocalSensingRecovery(task,true);transition(task.localOperation,"running",null);vacuumJournal.validateCapture(task.capture.payload,null);
            event("sensing_reconciliation_intent",sensingReconciliation.intentRecord(task.id,task.localOperation,UUID.randomUUID().toString(),Instant.now().toString(),task.owner,"local simulator operator"));task.intentWritten=true;
            task.permit=sensingReconciliation.issuePermit(task.id,task.localOperation,task.capture.digest,task);
        }
        NativeVacuumSensing.Guard guard=()->{synchronized(this){requireLocalSensingRecovery(task,true);sensingReconciliation.requirePermit(task.permit,task);}};
        sensingReconciliation.withPermit(task.permit,task,()->{
            if(task.restartCapture!=null){runFaultedSensingRestart(task,guard);return null;}
            if(task.continuationCapture!=null){runFaultedSensingContinuation(task,guard);return null;}
            if(task.replacementCapture!=null){runFaultedSensingReplacement(task,guard);return null;}
            NativeVacuumSensing.Plan first=task.plans.get(0);String interventionId=(String)NativeSensingReconciliation.object(sensingReconciliation.snapshot(task.id),"local_action").get("intervention_id");
            try(NativeVacuumSources.SourceIntervention intervention=prepareSensingSourceIntervention(first.nozzle().getId(),(String)first.source().get("source_id"),interventionId,Set.of(),guard,null,
                    (event,facts)->{synchronized(this){guard.check();this.event(event,map("task_id",task.id,"operation_id",task.localOperation,"facts",facts));}})){
                List<NativeVacuumSensing.Plan> probes=sensingRecoveryPlans();Map<String,Object> empty=map("state","empty","original_operation_id",null,"original_action_id",null,"original_part_id",null);
                synchronized(this){guard.check();event("sensing_reconciliation_intervention",sensingReconciliation.interventionRecord(task.id,(List<?>)task.capture.payload.get("nozzle_bindings"),sensingRecoveryBindings(probes),List.of(),empty,empty));}
                List<Object> observed=probeSensingRecovery(task,guard,probes);
                synchronized(this){guard.check();
                    event("sensing_reconciliation_verified",sensingReconciliation.verifiedRecord(task.id,map("native_probe_operation_id",task.localOperation,"probes",observed)));task.verified=true;
                    task.dispositions=map("sensing","fresh-observed-empty","nozzle_material","none-present","feeder_loads",List.of(),"board_loads",List.of(),"replacement_attempt_id",null,"unresolved_dependencies",List.of());
                    refresh();finish(task.localOperation,"succeeded",map("task_id",task.id,"native_verification_returned",true,"resolution_pending_wrapper",true,"simulation_only",true));
                }
            }return null;
        });
    }
    private void runFaultedSensingRestart(PendingSensingReconciliation task,NativeVacuumSensing.Guard guard)throws Exception {
        // Recovery reducers can call back into the Bridge while holding their own
        // monitor. Take the Bridge first, as public progress readers do. No motion
        // or Swing publication belongs inside this metadata transaction.
        synchronized(this){
            guard.check();requireSensingReconciliationProfile(true);
            if(task.restartAttestation==null)throw new Fault("SENSING_RESTART_ATTESTATION_REQUIRED","A fresh local GUI attestation is required");
            task.restartAttestation.check();task.restartStage.requireCurrent();
            task.restartPermit=faultedJobReplacement.beginRestart(task.restartCapture,sensingReconciliation,task.permit,task);
            guard.check();faultedJobReplacement.disposePriorProcess(task.restartPermit);
            GuiSensingFixture.claimRestart(task.restartAttestation,task.restartPermit,(type,facts)->{synchronized(this){guard.check();event(type,facts);}});
            for(Object raw:NativeSensingReconciliation.list(NativeSensingReconciliation.object(NativeSensingReconciliation.object(task.restartCapture.payload,"progress"),"material"),"rows",64)){
                guard.check();materialLoads.observeRestart(task.restartPermit,(String)NativeSensingReconciliation.asObject(raw).get("old_load_id"));
            }
            guard.check();boardLoads.observeRestart(task.restartPermit,task.restartPermit.original(),task.restartPermit.candidate().job(),task.restartPermit.attemptId());
            guard.check();Job original=task.restartPermit.originalForHost();replaceJob(original);jobId=(String)task.restartCapture.jobContext().get("job_id");jobRevision=revision;
            vacuumJobAdmission=null;validatedMaterialRevision=null;validatedBoardLoadRevision=null;setJobState("outcome_unknown");
            plans.clear();planDeadlines.clear();placementPlans.clear();configurationPlans.clear();clearStructurePlans();
        }
        publishGuiJob();
        synchronized(this){
            guard.check();task.restartPermit.confirmOriginalHostAttachment();
            task.restartObservations=faultedJobReplacement.restartObservationFacts(task.restartPermit);
            refresh();finish(task.localOperation,"succeeded",map("task_id",task.id,"restart_observations",task.restartObservations,"completion_pending_wrapper",true,
                "requires_separate_continuation",true,"requires_fresh_validation",true,"execution_authority_restored",false,"simulation_only",true));
        }
    }
    private void runFaultedSensingReplacement(PendingSensingReconciliation task,NativeVacuumSensing.Guard guard)throws Exception {
        guard.check();requireTerminalSensingJob(task.originalJobOperation);
        Map<String,Object> source=NativeVacuumSources.reconciliationSnapshot(config);
        if(!NativeFaultedJobReplacement.same(source,task.capturedSource))throw new Fault("SENSING_RECOVERY_SOURCE_CHANGED","Captured source state changed before local recovery");
        requireTerminalSourceObservations(source,task.replacementCapture.dependencies());
        Map<String,Object> before=sensingMaterialLatch(source);
        List<String> disposalActions=new ArrayList<>();
        NativeVacuumSources.InterventionSink sink=(type,facts)->{synchronized(this){guard.check();event(type,map("task_id",task.id,"operation_id",task.localOperation,"facts",facts));}};
        NativeReplacementJob.Candidate fresh=NativeReplacementJob.build(config,job);
        try{
            synchronized(this){
                guard.check();fresh.requireCurrent();
                task.replacementPermit=faultedJobReplacement.begin(task.replacementCapture,fresh,sensingReconciliation,task.permit,task);
                faultedJobReplacement.retainCandidate(task.replacementPermit,fresh);
            }
            guard.check();
            if(replacementDocuments==null)replacementDocuments=new NativeReplacementDocuments(config,journalDir.resolve("native-replacement-documents"),journalDir);
            NativeReplacementDocuments.Context documentContext=new NativeReplacementDocuments.Context((String)task.replacementCapture.payload.get("job_id"),task.replacementPermit.attemptId(),task.localOperation,task.capture.digest);
            NativeReplacementDocuments.Saved savedReplacement=replacementDocuments.save(documentContext,fresh);
            synchronized(this){guard.check();faultedJobReplacement.recordDocument(task.replacementPermit,savedReplacement);}
            for(int index=0;index<task.plans.size();index++){
                guard.check();String nozzleId=task.plans.get(index).nozzle().getId();
                Map<String,Object> current=NativeVacuumSources.reconciliationSnapshot(config);
                String sourceId=(String)NativeSensingReconciliation.object(current,"source").get("source_id");
                Set<String> pending=new TreeSet<>(NativeSensingReconciliation.ids(current,"pending_observation_ids",256,false));
                String interventionId=UUID.randomUUID().toString();
                NativeVacuumSensing.Guard terminal=()->{guard.check();requireTerminalSourceObservations(current,task.replacementCapture.dependencies());};
                try(NativeVacuumSources.SourceIntervention intervention=prepareSensingSourceIntervention(nozzleId,sourceId,interventionId,pending,guard,terminal,sink)){
                    try{
                        sensingRecoveryEffect(task,"enable-disposal:"+nozzleId,guard,()->machine.setEnabled(true));
                        sensingRecoveryEffect(task,"home-disposal:"+nozzleId,guard,()->machine.home());
                        String action=UUID.randomUUID().toString();intervention.dispose(action,sink);disposalActions.add(action);
                    }finally{if(machine.isEnabled())disableSensingRecoveryFinally(task);}
                }
            }
            List<NativeVacuumSensing.Plan> probes=sensingRecoveryPlans();
            Map<String,Object> after=map("state","empty","original_operation_id",before.get("original_operation_id"),"original_action_id",before.get("original_action_id"),"original_part_id",before.get("original_part_id"));
            Map<String,Object> actualAfter=sensingMaterialLatch(NativeVacuumSources.reconciliationSnapshot(config));
            if(!"empty".equals(actualAfter.get("state")))throw new Fault("SENSING_RECOVERY_MATERIAL_UNKNOWN","Native source still has unresolved material");
            synchronized(this){guard.check();event("sensing_reconciliation_intervention",sensingReconciliation.interventionRecord(task.id,(List<?>)task.capture.payload.get("nozzle_bindings"),sensingRecoveryBindings(probes),disposalActions,before,after));}
            List<Object> observed=probeSensingRecovery(task,guard,probes);
            guard.check();fresh.requireCurrent();
            synchronized(this){
                guard.check();fresh.requireCurrent();
                faultedJobReplacement.replace(task.replacementPermit);
                Job replacement=faultedJobReplacement.replacementJob(task.replacementPermit);
                faultedJobReplacement.beginPublication(task.replacementPermit);
                fresh.publish();replaceJob(replacement);jobId=task.replacementPermit.attemptId();jobRevision=revision;
                vacuumJobAdmission=null;validatedMaterialRevision=null;validatedBoardLoadRevision=null;setJobState("prepared");
                plans.clear();planDeadlines.clear();placementPlans.clear();configurationPlans.clear();clearStructurePlans();
                task.dispositions=faultedJobReplacement.dispositions(task.replacementPermit.attemptId(),"disposed-synthetic");
            }
            publishGuiJob();guard.check();
            synchronized(this){
                faultedJobReplacement.finishPublication(task.replacementPermit);
                event("sensing_reconciliation_verified",sensingReconciliation.verifiedRecord(task.id,map("native_probe_operation_id",task.localOperation,"probes",observed)));task.verified=true;
                refresh();finish(task.localOperation,"succeeded",map("task_id",task.id,"native_verification_returned",true,"resolution_pending_wrapper",true,"replacement_job",jobSummary(),"requires_fresh_validation",true,"simulation_only",true));
            }
        }finally{if(!faultedJobReplacement.ownsCandidate(fresh))fresh.close();}
    }
    private void runFaultedSensingContinuation(PendingSensingReconciliation task,NativeVacuumSensing.Guard guard)throws Exception {
        guard.check();Map<String,Object> source=NativeVacuumSources.reconciliationSnapshot(config);
        if(!NativeFaultedJobReplacement.same(source,task.capturedSource))throw new Fault("SENSING_RECOVERY_SOURCE_CHANGED","Continuation source state changed before the local decision");
        Map<String,Object> dependencies=task.continuationCapture.dependencies();requireTerminalSourceObservations(source,dependencies);
        // Retain the original candidate and exact captured phase union before any new intervention.
        synchronized(this){guard.check();task.continuationPermit=faultedJobReplacement.beginContinuation(task.continuationCapture,sensingReconciliation,task.permit,task);}
        // Finish an interrupted detached-document save under this decision before any source or
        // machine intervention. A prior decision's failed save never grants current authority.
        if(replacementDocuments==null)replacementDocuments=new NativeReplacementDocuments(config,journalDir.resolve("native-replacement-documents"),journalDir);
        synchronized(this){guard.check();faultedJobReplacement.ensureContinuationDocument(task.continuationPermit,replacementDocuments);}
        guard.check();
        Map<String,Object> before=sensingMaterialLatch(source);List<String> disposalActions=new ArrayList<>();
        NativeVacuumSources.InterventionSink sink=(type,facts)->{synchronized(this){guard.check();event(type,map("task_id",task.id,"operation_id",task.localOperation,"facts",facts));}};
        for(NativeVacuumSensing.Plan plan:task.plans){
            guard.check();String nozzleId=plan.nozzle().getId();Map<String,Object> current=NativeVacuumSources.reconciliationSnapshot(config);
            String sourceId=(String)NativeSensingReconciliation.object(current,"source").get("source_id");Set<String> pending=new TreeSet<>(NativeSensingReconciliation.ids(current,"pending_observation_ids",256,false));
            String interventionId=UUID.randomUUID().toString();NativeVacuumSensing.Guard terminal=()->{guard.check();requireTerminalSourceObservations(current,dependencies);};
            try(NativeVacuumSources.SourceIntervention intervention=prepareSensingSourceIntervention(nozzleId,sourceId,interventionId,pending,guard,terminal,sink)){
                // Retiring an unfinished observation records new material uncertainty. Decide
                // disposal from that resulting state, even if an earlier decision already
                // disposed of material before its later probe was interrupted.
                boolean disposalRequired=!"empty".equals(sensingNozzleMaterialLatch(NativeVacuumSources.reconciliationSnapshot(config),nozzleId).get("state"));
                if(disposalRequired)try{
                    sensingRecoveryEffect(task,"enable-disposal:"+nozzleId,guard,()->machine.setEnabled(true));
                    sensingRecoveryEffect(task,"home-disposal:"+nozzleId,guard,()->machine.home());
                    String action=UUID.randomUUID().toString();intervention.dispose(action,sink);disposalActions.add(action);
                }finally{if(machine.isEnabled())disableSensingRecoveryFinally(task);}
            }
        }
        List<NativeVacuumSensing.Plan> probes=sensingRecoveryPlans();Map<String,Object> after=map("state","empty","original_operation_id",before.get("original_operation_id"),"original_action_id",before.get("original_action_id"),"original_part_id",before.get("original_part_id"));
        if(!"empty".equals(sensingMaterialLatch(NativeVacuumSources.reconciliationSnapshot(config)).get("state")))throw new Fault("SENSING_RECOVERY_MATERIAL_UNKNOWN","Continuation did not resolve current nozzle material");
        synchronized(this){guard.check();event("sensing_reconciliation_intervention",sensingReconciliation.interventionRecord(task.id,(List<?>)task.capture.payload.get("nozzle_bindings"),sensingRecoveryBindings(probes),disposalActions,before,after));}
        List<Object> observed=probeSensingRecovery(task,guard,probes);guard.check();
        synchronized(this){
            guard.check();faultedJobReplacement.continueReplacement(task.continuationPermit);
            task.continuationPublication=faultedJobReplacement.beginContinuationPublication(task.continuationPermit);
            Job candidate=faultedJobReplacement.bindContinuationForPublication(task.continuationPublication);
            replaceJob(candidate);jobId=task.continuationPermit.attemptId();jobRevision=revision;
            vacuumJobAdmission=null;validatedMaterialRevision=null;validatedBoardLoadRevision=null;setJobState("prepared");
            plans.clear();planDeadlines.clear();placementPlans.clear();configurationPlans.clear();clearStructurePlans();
        }
        publishGuiJob();guard.check();
        synchronized(this){
            faultedJobReplacement.finishContinuationPublication(task.continuationPublication);
            task.dispositions=faultedJobReplacement.continuationDispositions(task.continuationPublication.publicationId(),disposalActions.isEmpty()?"none-present":"disposed-synthetic");
            event("sensing_reconciliation_verified",sensingReconciliation.verifiedRecord(task.id,map("native_probe_operation_id",task.localOperation,"probes",observed)));task.verified=true;
            refresh();finish(task.localOperation,"succeeded",map("task_id",task.id,"native_verification_returned",true,"resolution_pending_wrapper",true,"replacement_job",jobSummary(),"requires_fresh_validation",true,"simulation_only",true));
        }
    }
    /** Source metadata invokes Bridge guards while holding the fixture monitor.
     * Match public readers' Bridge-before-fixture order; callers perform motion
     * and native disposal only after this method releases the Bridge monitor. */
    private synchronized NativeVacuumSources.SourceIntervention prepareSensingSourceIntervention(String nozzleId,String sourceId,String interventionId,
            Set<String> pending,NativeVacuumSensing.Guard guard,NativeVacuumSensing.Guard terminal,NativeVacuumSources.InterventionSink sink)throws Exception {
        guard.check();
        NativeVacuumSources.SourceIntervention intervention=pending.isEmpty()
            ?NativeVacuumSources.beginReconciliation(config,nozzleId,sourceId,interventionId,guard)
            :NativeVacuumSources.beginReconciliationAfterTerminalScope(config,nozzleId,sourceId,interventionId,pending,guard,terminal);
        try{
            if(!pending.isEmpty())intervention.retireTerminalObservations(sink);
            intervention.repairSource(sink);return intervention;
        }catch(Exception|Error failure){intervention.close();throw failure;}
    }
    private Map<String,Object> sensingNozzleMaterialLatch(Map<String,Object> source,String nozzleId)throws Exception {
        for(Object raw:NativeSensingReconciliation.list(source,"nozzles",64))if(nozzleId.equals(NativeSensingReconciliation.asObject(raw).get("nozzle_id")))return sensingMaterialLatch(map("nozzles",List.of(raw)));
        throw new Fault("SENSING_BINDING_REQUIRED","Current source has no exact nozzle binding");
    }
    private Map<String,Object> sensingMaterialLatch(Map<String,Object> source)throws Exception {
        Map<String,Object> result=map("state","empty","original_operation_id",null,"original_action_id",null,"original_part_id",null);int selected=0;
        for(Object raw:NativeSensingReconciliation.list(source,"nozzles",64)){
            Map<String,Object> row=NativeSensingReconciliation.asObject(raw),signal=NativeSensingReconciliation.object(row,"signal");
            boolean pending=!NativeSensingReconciliation.list(signal,"pending_observations",256).isEmpty();
            Nozzle nozzle=nozzle((String)row.get("nozzle_id"));
            // Disposal resolves the old latch, never a subsequent unfinished observation.
            if(Boolean.TRUE.equals(signal.get("disposed_by_native_action"))&&nozzle.getPart()==null&&!pending)continue;
            boolean unknown=Boolean.TRUE.equals(signal.get("material_unknown_observed"))||pending;
            int rank=unknown?3:Boolean.TRUE.equals(signal.get("retained_observed"))?2:Boolean.TRUE.equals(signal.get("lost_observed"))?1:0;
            if(rank==0&&nozzle.getPart()!=null)rank=3;
            if(rank<=selected)continue;selected=rank;
            Map<String,Object> origin=signal.get("fault_origin") instanceof Map?NativeSensingReconciliation.object(signal,"fault_origin"):Collections.emptyMap();
            Map<String,Object> context=origin.get("bridge_context") instanceof Map?NativeSensingReconciliation.object(origin,"bridge_context"):Collections.emptyMap();
            result=map("state",rank==3?"unknown":rank==2?"retained":"lost","original_operation_id",context.get("operation_id"),"original_action_id",origin.get("original_action_id"),"original_part_id",origin.get("original_part_id"));
        }
        return result;
    }
    private List<Object> probeSensingRecovery(PendingSensingReconciliation task,NativeVacuumSensing.Guard guard,List<NativeVacuumSensing.Plan> probes)throws Exception {
        List<Object> observed=new ArrayList<>();
        try{
            sensingRecoveryEffect(task,"enable",guard,()->machine.setEnabled(true));
            sensingRecoveryEffect(task,"home",guard,()->machine.home());
            for(NativeVacuumSensing.Plan plan:probes){
                sensingRecoveryEffect(task,"safe-z:"+plan.nozzle().getId(),guard,()->plan.nozzle().moveToSafeZ());
                Map<String,Object> context=sensingReconciliation.observationContext(task.permit,plan.nozzle().getId());
                List<String> reads=new ArrayList<>();String[] check={null},on={null},off={null};
                Map<String,Object> result=plan.verify("part_off",guard,(type,nozzle,data)->{try{synchronized(this){
                    boolean cleanup=type.startsWith("valve.")&&Boolean.FALSE.equals(data.get("enabled"));if(!cleanup)guard.check();
                    NativeVacuumSources.observe(config,type,nozzle,data,context);vacuumJournal.observe(type,nozzle,data,context,this::event);
                    String observation=(String)data.get("observation_id");if(type.equals("check.returned"))check[0]=observation;
                    if(type.equals("read.returned"))reads.add(observation);if(type.equals("valve.returned")){if(Boolean.TRUE.equals(data.get("enabled")))on[0]=observation;else off[0]=observation;}
                }}catch(NativeVacuumJournal.Fence retained){throw retained;}catch(Exception failure){vacuumJournal.invalidate(nozzle.getId(),"observer-fault");throw new NativeVacuumJournal.Fence("VACUUM_OUTCOME_UNKNOWN","Recovery native observation boundary failed",failure);}});
                if(!Boolean.TRUE.equals(result.get("native_verdict")))throw new Fault("SENSING_RECOVERY_PROBE_FAILED","Native part-off check did not establish empty state");
                observed.add(map("nozzle_id",plan.nozzle().getId(),"check_observation_id",check[0],"read_observation_ids",reads,"valve_on_id",on[0],"valve_off_id",off[0],"native_verdict",true,"exact_current_binding",NativeVacuumJournal.binding(context,map("nozzle_tip_id",plan.nozzle().getNozzleTip().getId(),"sensor_id",plan.nozzle().getVacuumSenseActuator().getId(),"source",plan.source()))));
            }
        }finally{if(machine.isEnabled())disableSensingRecoveryFinally(task);}
        return observed;
    }
    private interface SensingRecoveryEffect {void run()throws Exception;}
    private void disableSensingRecoveryFinally(PendingSensingReconciliation task)throws Exception {
        // Native valve-off and inert simulator disable remain cleanup obligations after a lease or journal failure.
        NativeVacuumSources.requireLifecycleSafe(config);verifyNativeSimulatorClasses(machine);
        if(Configuration.get()!=config||config.getMachine()!=machine||!machine.isTask(Thread.currentThread()))throw new Fault("SENSING_RECOVERY_CLEANUP_CONTEXT","Native cleanup context changed");
        Throwable publication=null;
        try{synchronized(this){event("sensing_recovery_native_intent",map("task_id",task.id,"operation_id",task.localOperation,"stage","disable","cleanup_attempt",true));}}
        catch(Exception|Error failure){publication=failure;}
        try{machine.setEnabled(false);}
        catch(Exception|Error failure){if(publication!=null)failure.addSuppressed(publication);throw failure;}
        try{synchronized(this){if(!journalFault)event("sensing_recovery_native_returned",map("task_id",task.id,"operation_id",task.localOperation,"stage","disable","cleanup_attempt",true));}}
        catch(Exception|Error failure){if(publication==null)publication=failure;else publication.addSuppressed(failure);}
        if(publication!=null)throw new IOException("Simulator disabled; recovery journal publication remains unresolved",publication);
    }
    private void markSensingReconciliationUnknown(PendingSensingReconciliation task,String reason)throws Exception {
        if(!task.unknownRecorded){event("sensing_reconciliation_unknown",sensingReconciliation.unknownRecord(task.id,reason));task.unknownRecorded=true;}
    }
    private void sensingRecoveryEffect(PendingSensingReconciliation task,String stage,NativeVacuumSensing.Guard guard,SensingRecoveryEffect effect)throws Exception {
        synchronized(this){guard.check();event("sensing_recovery_native_intent",map("task_id",task.id,"operation_id",task.localOperation,"stage",stage));}guard.check();effect.run();guard.check();
        synchronized(this){event("sensing_recovery_native_returned",map("task_id",task.id,"operation_id",task.localOperation,"stage",stage));}
    }
    private void completeSensingReconciliation(NativeSubmission submission,Map<String,Object> outcome,Throwable wrapperFailure,boolean cancelled)throws Exception {
        PendingSensingReconciliation task=pendingSensingReconciliation;if(task==null||!submission.operationId.equals(task.localOperation)||!task.intentWritten)return;
        try{
        // Terminal failure may later be continued with a new local decision. Preserve the actual
        // completed native wrapper, never treat Future cancellation as completion.
        if(!cancelled&&submission.entered&&submission.exited)sensingCompletedWrappers.put(submission.operationId,submission);
        if(task.restartCapture!=null&&wrapperFailure==null&&!cancelled&&"succeeded".equals(outcome.get("state"))&&task.restartObservations!=null){
            if(!task.active||epoch!=task.owner||session==null||System.nanoTime()>=task.deadline||task.localAuthority==null||!task.localAuthority.getAsBoolean()){
                markSensingReconciliationUnknown(task,"ownership_changed");return;
            }
            event("sensing_reconciliation_restart_observations_completed",sensingReconciliation.restartObservationsCompletedRecord(task.id,UUID.randomUUID().toString(),task.restartObservations));
            task.restartCompleted=true;return;
        }
        if(wrapperFailure==null&&!cancelled&&"succeeded".equals(outcome.get("state"))&&task.verified){
            // This branch is reached only after Future.get and the terminal operation's successful force.
            if(!task.active||epoch!=task.owner||session==null||System.nanoTime()>=task.deadline||task.localAuthority==null||!task.localAuthority.getAsBoolean()){
                markSensingReconciliationUnknown(task,"ownership_changed");return;
            }
            sensingCompletedWrappers.put(submission.operationId,submission);
            NativeSensingReconciliation.WrapperCompletion completion=sensingReconciliation.wrapperCompleted(task.id,submission.operationId);
            event("sensing_reconciliation_resolved",sensingReconciliation.resolvedRecord(task.id,UUID.randomUUID().toString(),task.dispositions,List.of()));
            sensingReconciliation.activateResolution(task.id,completion);task.resolved=true;
        }else markSensingReconciliationUnknown(task,"wrapper_unknown");
        }finally{
            if(task.replacementPermit!=null)task.replacementPermit.close();
            if(task.continuationPublication!=null)task.continuationPublication.close();
            if(task.continuationPermit!=null)task.continuationPermit.close();
            if(task.restartPermit!=null)task.restartPermit.close();
            if(task.restartAttestation!=null)task.restartAttestation.close();
            if(task.restartStage!=null)task.restartStage.close();
        }
    }

    private void requireInspectionProfile()throws Fault {
        if(guiOwnership==null||!guiOwnership.supportsLoadedBoardInspection())throw new Fault("INSPECTION_GUI_REQUIRED","This workflow requires the local simulator inspection form");
    }
    private Map<String,Object> inspectionContext()throws Exception {
        Map<String,Object> lineage=lineageAdmissionFacts();
        return map("bridge_instance_id",instance,"machine_id",machineId,"job_id",jobId,"job_revision",boardLoads.jobRevision(),
            "config_revision",revisionString(),"board_load_revision",boardLoads.revision(),"lineage_id",lineage.get("lineage_id"),
            "lineage_revision",lineage.get("lineage_revision"),"ownership_epoch",epoch,"job_state",jobState);
    }
    private void requireInspectionNative()throws Exception {
        requireInspectionProfile();
        if(!machine.isTask(Thread.currentThread()))throw new Fault("INSPECTION_EXECUTOR","Inspection requires the owning native executor");
        if(machine.isEnabled())throw new Fault("INSPECTION_MACHINE_ENABLED","Disable the simulator before inspection");
        if(job==null||!"completed".equals(jobState))throw new Fault("INSPECTION_JOB_INCOMPLETE","Complete the current native job before inspection");
        requireEmptyNozzles();
    }
    private synchronized Object captureBoardInspection(JsonObject p)throws Exception {
        requireInspectionNative();requireJobEditScope(p);fence(epoch);
        if(pendingInspection!=null&&pendingInspection.active)throw new Fault("INSPECTION_PENDING","A local inspection is already pending");
        String id=UUID.randomUUID().toString(),operation=nativeOperationContext.get(),loaded=text(p,"loaded_board_id");
        Map<String,Object> context=inspectionContext();NativeLoadedBoardInspection.Snapshot captured;
        try{captured=NativeLoadedBoardInspection.capture(config,job,boardLoads.ledgerScope(job),context,loaded);}
        catch(NativeLoadedBoardInspection.Fault e){throw new Fault(e.code,e.getMessage());}
        Instant created=Instant.now();Map<String,Object> task=map("profile",NativeLoadedBoardInspection.PROFILE,"schema_version",1,"task_id",id,
            "request_operation_id",operation,"request_id",text(p,"request_id"),"machine_id",machineId,"bridge_instance_id",instance,
            "created_at",created.toString(),"expires_at",created.plusSeconds(300).toString(),"context",context,"snapshot",captured.preview());
        event("inspection_task_pending",task);
        pendingInspection=new PendingInspection(id,operation,loaded,epoch,captured,task);
        return map("task_id",id,"profile",NativeLoadedBoardInspection.PROFILE,"state","awaiting-local-observations","expires_at",task.get("expires_at"),
            "snapshot",captured.preview(),"production_authority_granted",false,"operator_label_verified",false);
    }
    /** Called with the Bridge lock. This only compares cached values and schedules the local UI. */
    private void inspectionMaintenance()throws Exception {
        PendingInspection task=pendingInspection;if(task==null)return;
        if(task.localOperation!=null){
            Map<String,Object> op=operations.get(task.localOperation);
            boolean unresolved=pendingSubmission!=null&&task.localOperation.equals(pendingSubmission.operationId);
            if(!task.completionDelivered&&((!unresolved&&op!=null&&Set.of("succeeded","failed","outcome_unknown").contains(op.get("state")))||journalFault)){
                task.completionDelivered=true;task.active=false;
                guiOwnership.dismissLoadedBoardInspection(task.id,"submission-finished");
                if(!task.intentWritten&&task.closeReason==null&&!journalFault){event("inspection_task_closed",map("task_id",task.id,"reason","request-failed"));task.closeReason="request-failed";}
                Map<String,Object> receipt=map("operation",operation(task.localOperation),"inspection",inspectionJournal.get(task.id,instance));
                Throwable error=journalFault?new Fault("INSPECTION_PUBLICATION_FAILED","Inspection completion remains unresolved; preserve the journal"):
                    "succeeded".equals(op.get("state"))?null:new Fault("INSPECTION_SUBMISSION_FAILED","Local submission failed; inspect the retained operation before proceeding");
                deliverInspection(task,receipt,error);
            }
            return;
        }
        if(!task.active)return;
        Map<String,Object> context=(Map<String,Object>)task.task.get("context");
        String invalid=closed||journalFault||session==null||epoch!=task.owner?"ownership-revoked":System.nanoTime()>=task.deadline?"expired":
            !Objects.equals(context.get("config_revision"),revisionString())||!Objects.equals(context.get("job_id"),jobId)||
            !Objects.equals(context.get("job_revision"),boardLoads.jobRevision())||!Objects.equals(context.get("board_load_revision"),boardLoads.revision())||!"completed".equals(jobState)?"scope-stale":null;
        if(invalid!=null){closeInspection(task,invalid);return;}
        Map<String,Object> op=operations.get(task.requestOperation);
        if(op==null||Set.of("failed","outcome_unknown","cancelled").contains(op.get("state"))){closeInspection(task,"request-failed");return;}
        if(!task.presented&&pendingSubmission==null&&"succeeded".equals(op.get("state"))){
            task.presented=true;
            try{guiOwnership.presentLoadedBoardInspection(freezeDto(task.task),new GuiOwnership.InspectionSubmission(){
                public CompletionStage<Map<String,Object>> submit(Map<String,Object> observations,java.util.function.BooleanSupplier authority){return submitLocalInspection(task,observations,authority);}
                public void cancel(String reason){synchronized(Bridge.this){try{closeInspection(task,"cancelled");}catch(Exception failure){journalFault=true;pauseRequested=true;}}}
            });}catch(Exception failure){closeInspection(task,"presentation-failed");}
        }
    }
    private void closeInspection(PendingInspection task,String reason)throws Exception {
        if(task!=pendingInspection||!task.active||task.used)return;
        task.active=false;task.closeReason=reason;
        if(guiOwnership!=null)guiOwnership.dismissLoadedBoardInspection(task.id,reason);
        CompletableFuture<Map<String,Object>> queued=task.queued.get();
        if(queued!=null&&!queued.isDone())queued.completeExceptionally(new IllegalStateException("Local inspection closed: "+reason));
        if(!journalFault&&!closed)event("inspection_task_closed",map("task_id",task.id,"reason",reason));
    }
    private void requireLocalInspection(PendingInspection task,java.util.function.BooleanSupplier authority,boolean submitted)throws Exception {
        if(task!=pendingInspection||!task.active||task.used!=submitted||System.nanoTime()>=task.deadline||closed||journalFault||authority==null||!authority.getAsBoolean())
            throw new Fault("INSPECTION_AUTHORITY_STALE","The one-time local inspection view is no longer current");
        fence(task.owner);guiOwnership.requireRemoteGrant();
    }
    private CompletionStage<Map<String,Object>> submitLocalInspection(PendingInspection task,Map<String,Object> observations,java.util.function.BooleanSupplier authority){
        CompletableFuture<Map<String,Object>> completion=new CompletableFuture<>();
        if(closed){completion.completeExceptionally(new IllegalStateException("Bridge closed"));return completion;}
        for(;;){CompletableFuture<Map<String,Object>> previous=task.queued.get();
            if(previous!=null&&!previous.isDone()){completion.completeExceptionally(new IllegalStateException("Local inspection submission is already queued"));return completion;}
            if(task.queued.compareAndSet(previous,completion))break;
        }
        // This entrypoint must not wait on the native/Bridge monitor from the GUI worker.
        inspectionCompletions.add(completion);completion.whenComplete((value,failure)->inspectionCompletions.remove(completion));
        if(closed){completion.completeExceptionally(new IllegalStateException("Bridge closed"));return completion;}
        try{httpThreads.execute(()->{
            try{
                synchronized(Bridge.this){
                    drainNativeCompletion();requireLocalInspection(task,authority,false);requireIdle();requireRequestCapacity();
                    String json=GSON.toJson(plain(observations));
                    if(json.getBytes(StandardCharsets.UTF_8).length>256*1024)throw new Fault("INSPECTION_LIMIT","Local submission exceeds 256 KiB");
                    JsonObject submitted=new JsonParser().parse(json).getAsJsonObject();
                    Map<String,Object> evaluated=task.captured.evaluate(submitted);
                    // Invalid form fields can be corrected. Once admitted, the private capability is consumed.
                    requireLocalInspection(task,authority,false);task.used=true;task.completion=completion;
                    String id=UUID.randomUUID().toString(),request=UUID.randomUUID().toString(),digest=sha256(json);task.localOperation=id;
                    Map<String,Object> op=map("operation_id",id,"request_id",request,"request_digest",digest,"method","local_native_inspection_submission",
                        "state","accepted","bridge_instance_id",instance,"config_revision",revisionString(),"accepted_at",Instant.now().toString(),"task_id",task.id);
                    event("operation",op);operations.put(id,op);requests.put(request,id);requestDigests.put(request,digest);activeOperation=id;
                    Map<String,Object> intent=map("task_id",task.id,"submission_operation_id",id,"request_id",request,"submission_sha256",digest,"scope_fingerprint",task.captured.preview().get("scope_fingerprint"));
                    try{submitNative(id,task.owner,false,false,()->{
                        nativeOperationContext.set(id);
                        try{synchronized(Bridge.this){
                            requireLocalInspection(task,authority,true);requireInspectionNative();transition(id,"running",null);
                            task.captured.revalidate(config,job,boardLoads.ledgerScope(job),inspectionContext(),task.loadedBoard);
                            List<Object> verified=verifyInspectionArtifacts(evaluated);
                            requireLocalInspection(task,authority,true);
                            task.captured.revalidate(config,job,boardLoads.ledgerScope(job),inspectionContext(),task.loadedBoard);
                            requireLocalInspection(task,authority,true);
                            event("inspection_submission_intent",intent);task.intentWritten=true;
                            byte[] bytes=GSON.toJson(plain(map("profile",NativeLoadedBoardInspection.PROFILE,"task_id",task.id,"submission_operation_id",id,
                                "submission_sha256",digest,"snapshot",task.captured.preview(),"submission",submitted,"result",evaluated,"verified_artifacts",verified))).getBytes(StandardCharsets.UTF_8);
                            if(bytes.length>1024*1024)throw new Fault("INSPECTION_LIMIT","Inspection receipt exceeds 1 MiB");
                            Map<String,Object> file=artifact("application/json",bytes,map("kind","native-loaded-board-inspection-receipt","task_id",task.id,"operation_id",id,
                                "artifact_bytes_verified",true,"instrument_authenticity_verified",false,"operator_label_verified",false,"production_authority_granted",false,"hardware_qualified",false));
                            forceAndReadInspectionArtifact(file);
                            requireLocalInspection(task,authority,true);requireInspectionNative();
                            task.captured.revalidate(config,job,boardLoads.ledgerScope(job),inspectionContext(),task.loadedBoard);
                            Map<String,Object> receipt=new LinkedHashMap<>(intent);receipt.put("artifact",file);receipt.put("result",evaluated);
                            requireLocalInspection(task,authority,true);
                            event("inspection_receipt",receipt);task.receiptWritten=true;
                            finish(id,"succeeded",map("task_id",task.id,"artifact",file,"result",evaluated,"production_authority_granted",false));
                        }}catch(Exception failure){synchronized(Bridge.this){try{
                            finish(id,task.intentWritten&&!task.receiptWritten?"outcome_unknown":"failed",map("code",inspectionErrorCode(failure),"message",safeMessage(failure),
                                "receipt_recorded",task.receiptWritten,"native_placed_history_modified",false,"production_authority_granted",false));
                        }catch(Exception publication){journalFault=true;pauseRequested=true;}}}finally{nativeOperationContext.remove();}
                        return null;
                    },true);}catch(Exception admission){finish(id,"failed",map("code","NATIVE_ADMISSION_REJECTED","message",safeMessage(admission),"native_effect_started",false));}
                }
            }catch(Throwable failure){
                synchronized(Bridge.this){if(task.used&&task.completion==completion){task.active=false;task.completionDelivered=true;guiOwnership.dismissLoadedBoardInspection(task.id,"submission-failed");if(task.localOperation!=null&&operations.containsKey(task.localOperation))journalFault=true;}}
                completion.completeExceptionally(failure);
            }
        });}catch(RejectedExecutionException closed){completion.completeExceptionally(closed);}
        return completion;
    }
    private static String inspectionErrorCode(Exception failure){return failure instanceof NativeLoadedBoardInspection.Fault?((NativeLoadedBoardInspection.Fault)failure).code:failure instanceof Fault?((Fault)failure).code:"INSPECTION_ERROR";}
    private List<Object> verifyInspectionArtifacts(Map<String,Object> result)throws Exception {
        List<Object> verified=new ArrayList<>();long total=0;
        for(Object raw:(List<?>)result.get("artifact_refs")){
            Map<String,Object> ref=(Map<String,Object>)raw;String id=(String)ref.get("artifact_id");requireMaterialUuid(id);
            Path metadata=journalDir.resolve(id+".metadata.json");
            if(!Files.isRegularFile(metadata,LinkOption.NOFOLLOW_LINKS)||Files.size(metadata)>65536)throw new Fault("INSPECTION_ARTIFACT","Native artifact metadata is unavailable");
            Map<String,Object> descriptor=NativeJournalJson.parseObject(Files.readString(metadata,StandardCharsets.UTF_8));
            if(!id.equals(descriptor.get("artifact_id"))||!ref.get("sha256").equals(descriptor.get("sha256")))throw new Fault("INSPECTION_ARTIFACT","Native evidence identity or digest differs");
            String mime=String.valueOf(descriptor.get("mime_type")),kind=(String)ref.get("kind");
            boolean allowed=kind.equals("image")?Set.of("image/png","image/jpeg").contains(mime):kind.equals("measurement-file")?Set.of("application/json","text/csv","text/plain").contains(mime):"text/plain".equals(mime);
            if(!allowed)throw new Fault("INSPECTION_ARTIFACT","Artifact MIME type does not match its declared evidence kind");
            byte[] bytes=forceAndReadInspectionArtifact(descriptor);total+=bytes.length;if(total>32L*1024*1024)throw new Fault("INSPECTION_LIMIT","Evidence content exceeds 32 MiB");
            verified.add(map("artifact_id",id,"sha256",ref.get("sha256"),"mime_type",mime,"size",bytes.length,"kind",kind));
        }
        return verified;
    }
    private byte[] forceAndReadInspectionArtifact(Map<String,Object> descriptor)throws Exception {
        String id=String.valueOf(descriptor.get("artifact_id"));requireMaterialUuid(id);Path data=journalDir.resolve(id+".artifact"),metadata=journalDir.resolve(id+".metadata.json");
        if(!Files.isRegularFile(data,LinkOption.NOFOLLOW_LINKS)||!Files.isRegularFile(metadata,LinkOption.NOFOLLOW_LINKS)||Files.size(data)>MAX_REQUEST_BYTES||Files.size(metadata)>65536)
            throw new Fault("INSPECTION_ARTIFACT","Evidence must be bounded regular native artifact files");
        for(Path path:List.of(data,metadata))try(FileChannel channel=FileChannel.open(path,StandardOpenOption.WRITE,LinkOption.NOFOLLOW_LINKS)){channel.force(true);}
        NativePortableConfiguration.forceDirectory(journalDir);
        byte[] bytes=NativePortableConfiguration.read(data,MAX_REQUEST_BYTES);
        Map<String,Object> disk=NativeJournalJson.parseObject(Files.readString(metadata,StandardCharsets.UTF_8));
        if(!sha256(bytes).equals(descriptor.get("sha256"))||NativeJournalJson.integer(descriptor.get("size"),0,MAX_REQUEST_BYTES)!=bytes.length||
            !Objects.equals(descriptor.get("artifact_id"),disk.get("artifact_id"))||!Objects.equals(descriptor.get("sha256"),disk.get("sha256"))||
            !Objects.equals(descriptor.get("mime_type"),disk.get("mime_type"))||NativeJournalJson.integer(disk.get("size"),0,MAX_REQUEST_BYTES)!=bytes.length)
            throw new Fault("INSPECTION_ARTIFACT","Durable native artifact readback differs from its receipt");
        return bytes;
    }
    private void deliverInspection(PendingInspection task,Map<String,Object> value,Throwable failure){
        Runnable deliver=()->{if(failure==null)task.completion.complete(value);else task.completion.completeExceptionally(failure);};
        try{httpThreads.execute(deliver);}catch(RejectedExecutionException closed){task.completion.completeExceptionally(new IllegalStateException("Bridge closed before inspection completion delivery"));}
    }

    private void settleFailure(String id,Exception failure) {
        Exception barrierFailure=null;
        try{machine.getMotionPlanner().waitForCompletion(null,CompletionType.WaitForStillstand);}catch(Exception e){barrierFailure=e;}
        synchronized(this){try{
            refresh();boolean effectUnknown=Boolean.TRUE.equals(operations.get(id).get("native_effect_pending"));String state=barrierFailure==null&&!effectUnknown?"failed":"outcome_unknown";
            if("outcome_unknown".equals(state)&&Objects.equals(activeOperation,id)&&job!=null)setJobState("outcome_unknown");
            finish(id,state,map("code",failure instanceof Fault?((Fault)failure).code:"NATIVE_ERROR","message",safeMessage(failure),"effect_outcome_unknown",effectUnknown,
                "completion",map("standstill_confirmed",barrierFailure==null,"barrier_error",barrierFailure==null?null:safeMessage(barrierFailure))));
        }catch(Exception e){journalFault=true;pauseRequested=true;}}
    }
    private void requireProfileChanges(JsonArray changes) throws Fault {
        if(!"sustained-workload".equals(simulatorProfile))return;
        for(JsonElement value:changes){if(value.isJsonObject()){JsonObject change=value.getAsJsonObject();
            if(change.has("type") && "set_job_planner_settings".equals(change.get("type").getAsString()) && change.has("job_order") && !"Unsorted".equals(change.get("job_order").getAsString()))throw new Fault("SIMULATOR_PROFILE_MISMATCH","The sustained profile requires native Unsorted order");}}
    }
    public synchronized void acceptLocalGuiGrant(Job currentGuiJob) throws Exception {
        if(guiOwnership==null)throw new Fault("GUI_UNAVAILABLE","No GUI adapter");
        drainNativeCompletion();simulatorGuard();requireNoPendingSubmission();
        if(activeOperation!=null || machine.isBusy())throw new Fault("BUSY","Cannot replace native job ownership while work remains");
        revision++;epoch++;configurationPlans.clear();clearStructurePlans();session=null;plans.clear();planDeadlines.clear();placementPlans.clear();clearStructurePlans();configurationPlans.clear();clearStructurePlans();replaceJob(currentGuiJob);
        jobId=job==null?null:UUID.randomUUID().toString();setJobState(job==null?"absent":"prepared");jobRevision=revision;
        if(job!=null)boardLoads.bindJob(job,jobId,false);
        event("gui_local_grant",map("ownership_epoch",epoch,"config_revision",revisionString(),"job_id",jobId,"physical_qualification",false));refresh();
    }
    public synchronized void requestLocalGuiTakeover(String reason) throws Exception {
        if(guiOwnership==null)throw new Fault("GUI_UNAVAILABLE","No GUI adapter");
        session=null;epoch++;configurationPlans.clear();clearStructurePlans();pauseRequested=true;abortRequested=true;
        invalidateVacuumReadiness("ownership-change");event("gui_local_takeover_requested",map("ownership_epoch",epoch,"reason",reason,"cooperative",true));
    }
    public synchronized boolean drainLocalGuiTakeover() throws Exception {
        drainNativeCompletion();if(pendingSubmission!=null){if(pendingSubmission.publicationAttempted)throw new Fault("RECOVERY_REQUIRED","Native completion is unresolved; ownership remains fenced");return false;}
        if(machine.isBusy())return false;
        if(activeOperation!=null && "paused".equals(jobState)){
            setJobState("aborting");transition(activeOperation,"accepted",null);submitJob(activeOperation,epoch,true);return false;
        }
        if(activeOperation!=null)return false;
        // A completed current-process reconciliation may release its exact old
        // scope. Historical receipts alone never grant this live disposition.
        for(Map<String,Object> op:operations.values())if("outcome_unknown".equals(op.get("state"))&&!sensingReconciliation.liveDisposesOperation((String)op.get("operation_id"),instance))throw new Fault("RECOVERY_REQUIRED","Unknown operation keeps GUI ownership fenced; preserve evidence and restart the isolated simulator");
        if(journalFault || configurationFault)throw new Fault("RECOVERY_REQUIRED","Faulted state keeps GUI ownership fenced");
        event("gui_local_takeover_ready",map("ownership_epoch",epoch,"native_busy",false,"job_state",jobState));return true;
    }
    public synchronized void pauseFromLocalGui() throws Exception {pauseRequested=true;event("gui_local_pause_requested",map("cooperative",true,"operation_id",activeOperation));}
    public synchronized void recordGuiUnknownExit() throws Exception {explicitUnknownExit=true;session=null;epoch++;configurationPlans.clear();clearStructurePlans();pauseRequested=true;event("gui_exit_preserving_unknown",map("ownership_epoch",epoch,"native_cleanup_performed",false,"unknown_outcomes_resolved",false,"configuration_saved",false));}

    private static void requireMaterialUuid(String id)throws Fault{try{if(!UUID.fromString(id).toString().equals(id))throw new IllegalArgumentException();}catch(IllegalArgumentException invalid){throw new Fault("INVALID_ARGUMENT","Canonical UUID required");}}
    private void requireMaterialConfigurationUnenrolled()throws Fault{
        if(materialLoads.hasEnrolled())throw new Fault("MATERIAL_CONFIGURATION_LOCKED","Private slice preserves enrolled material history: configuration edits/restores require a new isolated state");
    }
    private synchronized void beginConfigurationChange()throws Exception {
        invalidateVacuumReadiness("configuration-change");vacuumJobAdmission=null;
        revision++;if(job!=null)revokeJobValidation();plans.clear();planDeadlines.clear();placementPlans.clear();clearStructurePlans();configurationPlans.clear();clearStructurePlans();
        event("configuration_change_started",map("config_revision",revisionString()));
    }
    /** Dedicated non-retryable fence for post-admission axis/topology/camera configuration Error failures. */
    private static final class ConfigurationMutationFence extends Error {ConfigurationMutationFence(Error cause){super("Native configuration may be partial; never replay",cause);}}
    private synchronized void invalidateRegistrationDependencies(String reason)throws Exception {
        validatedBoardLoadRevision=null;
        boardLoads.invalidateRegistration(revisionString(),reason);
        if(job!=null)NativeBoardLoads.invalidateTransforms(job);
    }
    private synchronized void invalidateAxisDependencies(String reason)throws Exception {
        invalidateRegistrationDependencies(reason);
        machine.setHomed(false);
        ReferenceNozzleTipCalibration.resetAllNozzleTips();
        for(Camera camera:machine.getAllCameras())if(camera instanceof org.openpnp.machine.reference.camera.ReferenceCamera)((org.openpnp.machine.reference.camera.ReferenceCamera)camera).getAdvancedCalibration().setValid(false);
    }
    private synchronized Map<String,Object> lineageReadback(){
        try{return (Map<String,Object>)plain(jobLineage.describe(jobId));}
        catch(Exception failure){return map("status","faulted","structural_authority",false,"reason","LINEAGE_FAULT");}
    }
    private synchronized Map<String,Object> lineageAdmissionFacts()throws Exception {
        try{return jobLineage.admissionFacts(jobId);}catch(NativeJobLineage.Fault failure){throw new Fault(failure.code,failure.getMessage());}
    }
    private synchronized void lineageContentWillChange()throws Exception {
        Map<String,Object> facts=lineageReadback();
        if("unknown".equals(facts.get("status")))return;
        try{jobLineage.contentWillChange(jobId,nativeOperationContext.get());}catch(NativeJobLineage.Fault failure){throw new Fault(failure.code,failure.getMessage());}
    }
    private final class StructureAdapter implements NativePlacementEdits.StructureAuthority,NativePlacementEdits.PanelPublicationFence,AutoCloseable {
        final Job ownedJob;final String ownedJobId,configRevision,modelRevision,loadRevision;final long owner;
        NativeJobLineage.PendingPermit permit;boolean reservationAttempted;
        StructureAdapter(){ownedJob=job;ownedJobId=jobId;configRevision=revisionString();modelRevision=boardLoads.jobRevision();loadRevision=boardLoads.revision();owner=epoch;}
        public NativePlacementEdits.StructureState requireUnexecuted(Configuration current,Job selected)throws Exception {
            synchronized(Bridge.this){
                fence(owner);
                if(current!=config||config!=Configuration.get()||selected!=ownedJob||job!=ownedJob||!Objects.equals(jobId,ownedJobId)||!machine.isTask(Thread.currentThread()))throw new Fault("STRUCTURE_AUTHORITY_STALE","Exact current native job and executor ownership are required");
                if(!Objects.equals(configRevision,revisionString())||!Objects.equals(modelRevision,boardLoads.jobRevision())||!Objects.equals(loadRevision,boardLoads.revision()))throw new Fault("STRUCTURE_AUTHORITY_STALE","Native configuration/job/load revisions changed");
                if(machine.isEnabled())throw new Fault("STRUCTURE_MACHINE_ENABLED","Disable the native simulator before structure editing");
                requireEmptyNozzles();if(!NativeBoardLoads.fullHistoryAvailable())throw new Fault("STRUCTURE_HISTORY_UNAVAILABLE","Complete native placed-history API required");
                if(!ownedJob.getPlacedStatusSnapshot().isEmpty())throw new Fault("STRUCTURE_HISTORY_PRESENT","Any native placed-history key prevents structure editing");
                NativeJobLineage.Snapshot state;
                try{state=jobLineage.requirePristine(ownedJobId,permit);}catch(NativeJobLineage.Fault failure){throw new Fault(failure.code,failure.getMessage());}
                boardLoads.publishSnapshot();Map<String,Object> loadState=boardLoads.snapshot();
                if(!Boolean.TRUE.equals(loadState.get("complete_native_history"))||boardLoads.hasActiveUnknown())throw new Fault("LINEAGE_HISTORY_UNKNOWN","Complete settled native board-load history required");
                Set<String> remaining=new HashSet<>(state.loadIds);
                for(Object raw:(List<?>)loadState.get("loads")){Map<?,?> load=(Map<?,?>)raw;if(!remaining.remove(load.get("load_id")))continue;
                    if(!Boolean.TRUE.equals(load.get("complete_native_history"))||!(load.get("native_history_entries") instanceof Number)||((Number)load.get("native_history_entries")).longValue()!=0||"loading_unknown".equals(load.get("state")))throw new Fault("LINEAGE_HISTORY_PRESENT","Associated current or retired load has history or uncertainty");
                }
                if(!remaining.isEmpty())throw new Fault("LINEAGE_HISTORY_UNKNOWN","Associated load history is unavailable");
                return new NativePlacementEdits.StructureState(state.lineageId,state.revision,state.reservedLogicalIds);
            }
        }
        public void reserveAndForce(NativePlacementEdits.StructureState expected,JsonObject reservation)throws Exception {
            synchronized(Bridge.this){
                NativePlacementEdits.StructureState current=requireUnexecuted(config,ownedJob);
                if(!current.lineageId.equals(expected.lineageId)||current.revision!=expected.revision||!current.reservedLogicalIds.equals(expected.reservedLogicalIds))throw new Fault("LINEAGE_STALE","Lineage changed after preview");
                List<String> ids=new ArrayList<>();for(JsonElement id:reservation.getAsJsonArray("logical_ids"))ids.add(id.getAsString());
                reservationAttempted=true;
                permit=jobLineage.reserve(ownedJobId,expected.revision,reservation.get("transaction_id").getAsString(),reservation.get("source_fingerprint").getAsString(),ids);
            }
        }
        public void invalidateBeforePublication(Job selected,List<String> roots)throws Exception {
            synchronized(Bridge.this){
                requireUnexecuted(config,selected);
                setJobState("prepared");validatedBoardLoadRevision=null;jobCountsDirty=true;
                plans.clear();planDeadlines.clear();placementPlans.clear();configurationPlans.clear();clearStructurePlans();
                boardLoads.invalidateRegistration(revisionString(),"placement-structure-publication");NativeBoardLoads.invalidateTransforms(selected);
            }
        }
        public void publishIfCurrent(Configuration current,Job selected,NativePlacementEdits.StructureState expected,NativePlacementEdits.Publication publication)throws Exception {
            synchronized(Bridge.this){
                NativePlacementEdits.StructureState observed=requireUnexecuted(current,selected);
                if(!observed.lineageId.equals(expected.lineageId)||observed.revision!=expected.revision||!observed.reservedLogicalIds.equals(expected.reservedLogicalIds))throw new Fault("LINEAGE_STALE","Lineage changed before panel publication");
                if(publication==null)throw new Fault("STRUCTURE_PUBLICATION_PROTOCOL","An exact native publication callback is required");
                publication.publish();
                // Reentrant listeners and elapsed lease time can revoke authority during notification.
                // A setter may already have run; failure is retained by the reserved-publication fence.
                fence(owner);
                if(config!=current||config!=Configuration.get()||job!=selected||job!=ownedJob||!Objects.equals(jobId,ownedJobId)||!Objects.equals(configRevision,revisionString())||!machine.isTask(Thread.currentThread()))throw new Fault("STRUCTURE_AUTHORITY_STALE","Native authority changed during panel publication");
                if(machine.isEnabled())throw new Fault("STRUCTURE_MACHINE_ENABLED","Native simulator enabled during panel publication");
                requireEmptyNozzles();
            }
        }
        public void close(){if(permit!=null){permit.close();permit=null;}}
    }
    private final class StructurePlan {
        final NativePlacementEdits.StagedStructure patch;final StructureAdapter authority;
        StructurePlan(NativePlacementEdits.StagedStructure patch,StructureAdapter authority){this.patch=patch;this.authority=authority;}
    }
    private synchronized void discardStructurePlan(String id){StructurePlan plan=structurePlans.remove(id);if(plan!=null)plan.authority.close();}
    private synchronized void clearStructurePlans(){for(StructurePlan plan:structurePlans.values())plan.authority.close();structurePlans.clear();}
    private Object planStructure(JsonObject p)throws Exception {
        requireJobEditScope(p);only(p,"session_id","request_id","expected_config_revision","job_id","expected_job_revision","expected_board_load_revision","changes");
        if(!p.has("changes")||!p.get("changes").isJsonArray())throw new Fault("INVALID_CHANGES","Expected explicit structural changes");
        StructureAdapter authority=new StructureAdapter();NativePlacementEdits.StagedStructure patch;
        try{
            JsonArray changes=p.getAsJsonArray("changes");boolean panelChange=false;
            for(JsonElement element:changes)if(element.isJsonObject()){
                JsonElement action=element.getAsJsonObject().get("action");
                if(action!=null&&action.isJsonPrimitive()&&action.getAsJsonPrimitive().isString()&&Set.of("clone_board_child","remove_board_child").contains(action.getAsString()))panelChange=true;
            }
            if(panelChange){
                if(changes.size()!=1)throw new Fault("INVALID_CHANGES","Plan exactly one panel membership change without placement changes");
                patch=NativePlacementEdits.stagePanelStructure(config,job,changes.get(0).getAsJsonObject(),authority,authority);
            }else patch=NativePlacementEdits.stageStructure(config,job,changes,authority,authority);
        }
        catch(NativePlacementEdits.Fault failure){authority.close();throw new Fault(failure.code,failure.getMessage());}
        catch(Exception|Error failure){authority.close();throw failure;}
        JsonObject effects=patch.preview();effects.addProperty("current_bridge_lineage_support",true);
        synchronized(this){
            Map<String,Object> plan=map("kind","placement-structure","config_revision",revisionString(),"ownership_epoch",epoch,"job_id",jobId,"job_revision",boardLoads.jobRevision(),"board_load_revision",boardLoads.revision(),"effects",effects);
            String id=storePlan(plan);structurePlans.put(id,new StructurePlan(patch,authority));return map("plan_id",id,"plan",plan,"expires_in_ms",300000,"simulation_only",true);
        }
    }
    private Object applyStructure(JsonObject p)throws Exception {
        requireJobEditScope(p);only(p,"session_id","request_id","expected_config_revision","job_id","expected_job_revision","expected_board_load_revision","plan_id");
        StructurePlan staged;
        synchronized(this){
            Map<String,Object> plan=takePlan(p,"placement-structure");
            if(!Objects.equals(jobId,plan.get("job_id"))||!Objects.equals(boardLoads.jobRevision(),plan.get("job_revision"))||!Objects.equals(boardLoads.revision(),plan.get("board_load_revision")))throw new Fault("PLAN_STALE","Native job/load changed after structure preview");
            staged=structurePlans.remove(text(p,"plan_id"));if(staged==null)throw new Fault("PLAN_CONSUMED","Structure plan is expired or already attempted");
        }
        try{
            Map<String,Object> result=staged.patch.apply();
            boardLoads.jobChanged(job,jobId);publishGuiJob();
            String fingerprint=(String)NativePlacementEdits.inspect(config,job,0,1).get("source_fingerprint");
            synchronized(this){jobLineage.publicationSucceeded(jobId,((Number)result.get("lineage_revision")).longValue(),(String)result.get("transaction_id"),fingerprint);}
            result.put("source_fingerprint",fingerprint);result.put("job",jobSummary());result.put("job_revision",boardLoads.jobRevision());result.put("board_load_revision",boardLoads.revision());return result;
        }catch(Throwable failure){
            if(staged.authority.reservationAttempted){
                synchronized(this){String id=nativeOperationContext.get();Map<String,Object> operation=operations.get(id);if(operation!=null){operation.put("native_effect_pending",true);operation.put("native_effect_kind","native-placement-structure-publication");}configurationFault=true;}
                try{configurationFailed(failure);}catch(Exception suppressed){failure.addSuppressed(suppressed);}
            }
            if(failure instanceof NativePlacementEdits.Fault){NativePlacementEdits.Fault e=(NativePlacementEdits.Fault)failure;throw new Fault(e.code,e.getMessage());}
            if(failure instanceof NativeJobLineage.Fault){NativeJobLineage.Fault e=(NativeJobLineage.Fault)failure;throw new Fault(e.code,e.getMessage());}
            if(failure instanceof Error)throw new ConfigurationMutationFence((Error)failure);
            throw (Exception)failure;
        }finally{staged.authority.close();}
    }

    private synchronized void configurationFailed(Throwable cause)throws Exception {
        configurationFault=true;event("configuration_fault",map("config_revision",revisionString(),"message",safeMessage(cause),"recovery",topologyFault?"fresh-portable-generation-from-topology-preimage":"restart-isolated-simulator"));
    }
    private void requireJobEditScope(JsonObject p)throws Fault {
        text(p,"expected_config_revision");requireRevision(p);
        if(job==null||!Objects.equals(jobId,text(p,"job_id")))throw new Fault("NO_JOB","Select the current native job");
        if(!Objects.equals(boardLoads.jobRevision(),text(p,"expected_job_revision")))throw new Fault("JOB_REVISION_CONFLICT","Native job changed");
        boardLoads.requireRevision(text(p,"expected_board_load_revision"));
    }
    private void requireEmptyNozzles()throws Fault {requireVacuumEmpty();for(Head head:machine.getHeads())for(Nozzle nozzle:head.getNozzles())if(nozzle.getPart()!=null)throw new Fault("NOZZLE_OCCUPIED","Changeover requires an empty native nozzle; reconcile held material first");}
    private synchronized void requireRevision(JsonObject p)throws Fault {
        if(p.has("expected_config_revision")&&!revisionString().equals(text(p,"expected_config_revision")))throw new Fault("REVISION_CONFLICT","Configuration changed");
    }
    private synchronized Object replayRequest(String method,JsonObject p)throws Exception {
        String requestId=text(p,"request_id");String existing=requests.get(requestId);if(existing==null)return null;
        if(!sha256(method+canonical(p)).equals(requestDigests.get(requestId)))throw new Fault("REQUEST_ID_CONFLICT","Request ID was used with different arguments");
        return operation(existing);
    }
    private synchronized void beginCommand(String method,JsonObject p,String operationId)throws Exception {
        requireRequestCapacity();
        String requestId=text(p,"request_id"),digest=sha256(method+canonical(p));
        Map<String,Object> receipt=map("request_id",requestId,"request_digest",digest,"method",method,"operation_id",operationId,"state","accepted","bridge_instance_id",instance);
        event("command_receipt",receipt);commandReceipts.put(requestId,receipt);requests.put(requestId,operationId);requestDigests.put(requestId,digest);
    }
    private synchronized void completeCommand(JsonObject p)throws Exception {
        Map<String,Object> receipt=commandReceipts.get(text(p,"request_id"));receipt.put("state","succeeded");event("command_receipt",receipt);
    }

    /** Same monitor as expiry/ownership clearing: retained native references are never read concurrently. */
    private synchronized NativeSettings.Patch takeConfigurationPatch(JsonObject p,Map<String,Object> plan)throws Exception {
        takePlan(p,"configuration");
        NativeSettings.Patch patch=configurationPlans.remove(text(p,"plan_id"));
        if(patch==null&&Boolean.TRUE.equals(plan.get("retained_native_guard")))throw new Fault("PLAN_CONSUMED","Original native guard is absent; stage a new plan");
        return patch!=null?patch:NativeSettings.stage(config,(JsonArray)plan.get("changes"));
    }
    private synchronized Map<String,Object> takePlan(JsonObject p,String kind) throws Exception {
        expirePlans();
        Map<String,Object> plan=plans.get(text(p,"plan_id"));
        if(plan==null||!kind.equals(plan.get("kind")))throw new Fault("NOT_FOUND","Unknown plan");
        if(!revisionString().equals(plan.get("config_revision")) || ((Number)plan.get("ownership_epoch")).longValue()!=epoch)throw new Fault("PLAN_STALE","Plan revision or ownership changed");
        return plan;
    }
    private synchronized Map<String,Object> capabilities() {
        if(controllerDiagnostic!=null)return map("protocol_version","1.0","schema_version",1,"bridge_version","0.1.0","openpnp_version",Main.getVersion(),"upstream_commit",UPSTREAM,"bridge_artifact_sha256",loadedBridgeSha256,"machine_id",machineId,"bridge_instance_id",instance,"simulation",true,"simulator_profile",NativeControllerJournal.PROFILE,"tools",CONTROLLER_TOOLS,"controller_diagnostic",controllerDiagnostic.cached(),"controller_history",controllerJournal.snapshot(),"hardware_qualified",false,"configuration_changes",Collections.emptyList(),"fixed_recipe",List.of("bind","connect:G21,G90","identify:M115","close"),"fresh_launch_required",true,"native_driver","org.openpnp.codex.prototype.tagged.OwnedTaggedGcodeDriver","controller_protocol","owned-tagged-gcode-v1","physical_standstill_verified",false,"motion_completion_observed",false);

        Map<String,Object> result=map("protocol_version","1.0","schema_version",1,"bridge_version","0.1.0","openpnp_version",Main.getVersion(),"upstream_commit",UPSTREAM,
            "bridge_artifact_sha256",loadedBridgeSha256,"bridge_artifact_hash_available",loadedBridgeSha256!=null,
            "limits",map("rpc_request_bytes",MAX_REQUEST_BYTES,"journal_record_bytes",MAX_JOURNAL_RECORD_BYTES,"canonical_placements",10000,"board_definitions",100,"panel_definitions",100,"root_instances",100,"expanded_instances",1000,"panel_depth",8,"event_page",500,"json_depth",64,"retained_requests",MAX_REQUESTS,"journal_bytes",MAX_JOURNAL_BYTES,"plans",MAX_PLANS,"plan_ttl_ms",300000,"artifact_metadata_cache",MAX_ARTIFACT_METADATA,"artifact_bytes",MAX_REQUEST_BYTES,"artifact_storage_bytes",MAX_ARTIFACT_STORAGE_BYTES,"running_job_snapshot_interval_ms",100),
            "retention",map("request_receipts","retained until journal capacity; then fail closed","artifacts","bytes stored on disk and read on demand; aggregate content cap 1 GiB fails admission without deleting evidence; preserved until explicit state-directory cleanup","plans","five-minute TTL and 128 maximum; old plans expire"),
            "tools",Arrays.asList("openpnp_get_capabilities","openpnp_get_status","openpnp_get_configuration","openpnp_get_operation","openpnp_get_request_status","openpnp_get_events","openpnp_get_artifact","openpnp_reconcile_operation",
                "openpnp_request_control_session","openpnp_get_control_session","openpnp_renew_control_session","openpnp_release_control_session",
                "openpnp_plan_motion","openpnp_execute_motion","openpnp_set_machine_enabled","openpnp_home_machine","openpnp_capture_camera",
                "openpnp_plan_configuration","openpnp_apply_configuration","openpnp_prepare_job","openpnp_validate_job","openpnp_start_job","openpnp_step_job","openpnp_pause_job","openpnp_resume_job","openpnp_abort_job",
                "openpnp_export_portable_configuration","openpnp_save_job","openpnp_load_job","openpnp_get_board_loads","openpnp_register_board_load","openpnp_get_material_loads","openpnp_register_material_load","openpnp_inspect_job","openpnp_plan_placement_edits","openpnp_apply_placement_edits","openpnp_plan_placement_structure","openpnp_apply_placement_structure",
                "openpnp_test_feeder","openpnp_control_actuator","openpnp_change_nozzle_tip","openpnp_backup_configuration","openpnp_restore_configuration","openpnp_export_run_report",
                "openpnp_run_calibration","openpnp_validate_calibration","openpnp_locate_fiducials","openpnp_list_issues"),
            "machine_id",machineId,"bridge_instance_id",instance,"simulation",managedSimulator,"simulator_profile",simulatorProfile,"native_job_order",nativeJobOrder(),"simulator_attestation",map("mode","isolated-native-default","profile",simulatorProfile,"native_job_order",nativeJobOrder(),"job_order_rationale","sustained-workload".equals(simulatorProfile)?"preordered-grid; native Unsorted order avoids repeating whole-job route optimization each cycle":"upstream default order retained","fixture_provenance","sustained-workload".equals(simulatorProfile)?"native-finite-tray-supply-zero-pitch":"upstream-default-strip-feeders","driver_class","org.openpnp.machine.reference.driver.NullDriver","checked_at_dispatch",true,"acceleration_recipe",map("source","upstream SampleJobTest.makeMachineFastest","driver_feed_rate_mm_per_minute",0,"controller_axis_native_feedrate_per_second",1000000,"controller_axis_native_acceleration_per_second2",2000000,"controller_axis_native_jerk_per_second3",0,"fixed_camera_settling_ms",0,"nozzle_pick_place_dwell_ms",0,"physical_cycle_rate_comparison",false)),"hardware_qualified",false,"gui_control_qualified",false,
            "capabilities",map("observe","supported","native_simulator_execution",managedSimulator?"supported":"unsupported","hardware_execution","unqualified",
                "calibration",Boolean.TRUE.equals(cameraScaleRuntime.get("available"))?"native-nozzle-tip-runout-and-camera-planar-scale-measurement":"native-nozzle-tip-runout-only","configuration_restore","supported-typed-settings-only","custom_job_import","supported","per_placement_crash_recovery","unsupported"),
            "job_lineage",map("available",true,"scope","trusted fresh native simulator imports and durably associated signed documents","structural_profile","disabled-machine-empty-native-history-never-processor-admitted","unknown_documents","readable-without-structural-authority","physical_authority",false),
            "native_action_ledger",map("available",nativeLedgerAvailable,"coverage",nativeLedgerAvailable?NativeActionLedger.coverage():map("reason","stock runtime has no native action observer API")),
            "native_job_step",map("available",true,"boundary","one-native-processor-next-call","can_include_multiple_actions",true,
                "requires_config_revision",true,"pause_waits_for_native_standstill",true,"continuous_resume",true,"automatic_restart_resume",false),
            "board_load_authority",map("source","native-simulator","complete_history_api",NativeBoardLoads.fullHistoryAvailable(),"physical_load_verified",false,"restart_requires_explicit_binding",true,"replacement_resets_only_target_history",true,"flip_preserves_history",true,"explicit_changeover_available",NativeBoardLoads.fullHistoryAvailable(),"stock_runtime_scope","initial fresh simulator loads only; explicit changeover requires complete-history patch","unbound_saved_history","refused; loading a document does not identify a board"),
            "configuration_changes",NativeSettings.types(),
            "canonical_part_bindings",map("profile",NativePartBindings.PROFILE,"existing_parts_only",true,"max_bindings",1000,"scope","explicit import-local source-to-native IDs with package and height checks","library_mutation",false,"physical_equivalence_verified",false),
            "panel_board_membership",map("profile","panel-board-membership-v1","scope","one direct board child in one exact panel instance","actions",List.of("clone_board_child","remove_board_child"),"max_changes",1,"requires_pristine_lineage",true,"requires_complete_empty_history",true,"holder_identity_retirement",true,"all_root_confirmation_invalidated",true,"physical_fixture_qualified",false),
            "camera_settling",NativeCameraSettling.capabilities(),
            "camera_planar_scale",map("runtime",cameraScaleRuntime,"recipe_id",NativeCameraScaleMeasurement.RECIPE,"measurement_only",true,"requires_initialized_source",true,"automatic_application",false,"physical_qualification",false),
            "limitations",Arrays.asList("Only an isolated OpenPnP default simulator may mutate", "Native step journal does not prove every irreversible substep", "No physical machine profile is qualified", "GUI attachment requires its verified optional native patch and a visible local grant"));
        if("vacuum-sensing".equals(simulatorProfile)){Map<String,Object> attestation=(Map<String,Object>)result.get("simulator_attestation");attestation.put("mode","explicit-controlled-vacuum-simulator");attestation.put("fixture_provenance","native-finite-tray-supply-zero-pitch-and-process-owned-synthetic-vacuum");attestation.put("job_order_rationale","explicit vacuum fixture retains native Unsorted finite-tray order");attestation.put("vacuum_source",vacuumSources);attestation.put("physical_signal_measured",false);}
        result.put("fresh_fixture_startup",map("native_roundtrip_before_bridge",SimulatorMain.isSettledFreshFixture(config),"source_scope",SimulatorMain.isSettledFreshFixture(config)?"newly generated default simulator only":"not this startup path","enable_home_performed",false));
        List<String> inspectionTools=new ArrayList<>((List<String>)result.get("tools"));inspectionTools.add("openpnp_get_board_inspection");
        result.put("vacuum_sensing",vacuumSources);
        if(Boolean.TRUE.equals(vacuumSources.get("available"))){inspectionTools.add("openpnp_measure_sensor");inspectionTools.add("openpnp_verify_part_state");}
        boolean sensingRecoveryAvailable=guiOwnership!=null&&guiOwnership.supportsSensingReconciliation()&&(Boolean.TRUE.equals(vacuumSources.get("available"))||guiOwnership.supportsSensingRestart());
        boolean restartRequestAvailable=guiOwnership!=null&&guiOwnership.supportsSensingRestart()&&job==null&&!Boolean.TRUE.equals(NativeVacuumSources.reconciliationSnapshot(config).get("available"));
        result.put("sensing_reconciliation",map("profile",NativeSensingReconciliation.PROFILE,"available",sensingRecoveryAvailable,"restart_request_available",restartRequestAvailable,"qualified",false,"simulation_only",true,"hardware_qualified",false));
        inspectionTools.add("openpnp_get_sensing_reconciliation");
        if(sensingRecoveryAvailable)inspectionTools.add("openpnp_request_sensing_reconciliation");
        boolean localInspection=guiOwnership!=null&&guiOwnership.supportsLoadedBoardInspection();
        if(localInspection)inspectionTools.add("openpnp_request_board_inspection");result.put("tools",inspectionTools);
        result.put("loaded_board_inspection",map("profile",NativeLoadedBoardInspection.PROFILE,"request_available",localInspection,
            "submission_channel","one-time-local-gui-callback","completed_native_job_required",true,"disabled_machine_required",true,
            "max_placements",200,"max_artifact_refs",32,"retained_task_limit",64,"task_ttl_seconds",300,
            "authority","local-operator-reported","instrument_authenticity_verified",false,"operator_label_verified",false,
            "production_authority_granted",false,"hardware_qualified",false,"restart_authority_restored",false));
        if(!materialProfileAvailable()){List<String> available=new ArrayList<>((List<String>)result.get("tools"));available.removeAll(List.of("openpnp_get_material_loads","openpnp_register_material_load"));result.put("tools",available);}
        result.put("material_loads",map("available",materialProfileAvailable(),"scope","existing-same-part-finite-Normal-ReferenceTrayFeeder","physical_inventory_verified",false,"restart_reattachment","unsupported","retained_load_limit",512));
        result.put("portable_configuration",map("export","lease-bound-async-zip","adoption","dedicated-validation-jvm-fresh-instance-only","first_launch_only",true,"native_model_limits",NativePortableLimits.describe(),"panel_library",NativePortableLimits.describePanelLibrary(),"in_place_restore",false,"hardware_qualified",false));
        if(portableLaunch!=null){
            Map<String,Object> attestation=(Map<String,Object>)result.get("simulator_attestation");attestation.put("mode","adopted-native-simulator");attestation.put("fixture_provenance","validated-portable-native-configuration");attestation.put("job_order_rationale","adopted native configuration retained");attestation.put("acceleration_recipe",map("applied",false,"source","all adopted timing/axis/camera/nozzle settings retained","physical_cycle_rate_comparison",false));result.put("portable_adoption",portableLaunch.snapshot());
            result.put("limitations",Arrays.asList("One-time launch of the validated fresh adopted simulator; no automatic relaunch", "Source scripts are quarantined; active script evaluation is refused", "No source operational history or physical authority transfers", "No physical machine is qualified"));
        }
        if(guiOwnership!=null){
            Map<String,Object> attestation=(Map<String,Object>)result.get("simulator_attestation");
            attestation.put("mode","gui-native-simulator");attestation.put("fixture_provenance","operator-configured-native-simulator");
            attestation.put("job_order_rationale","operator-selected supported native order");
            attestation.put("acceleration_recipe",map("source","native GUI configuration; attachment does not change timing settings","physical_cycle_rate_comparison",false));
            result.put("gui_ownership",guiOwnership.snapshot());
            result.put("limitations",Arrays.asList("GUI simulator adapter is a candidate pending native GUI qualification", "Native ownership API is not a sandbox for arbitrary in-process Java", "No physical machine is qualified", "Local takeover is cooperative and not an emergency stop"));
        }
        return result;
    }
    private String nativeJobOrder(){return machine.getPnpJobProcessor() instanceof ReferencePnpJobProcessor?((ReferencePnpJobProcessor)machine.getPnpJobProcessor()).getJobOrder().name():"unsupported";}
    private synchronized void simulatorGuard() throws Exception {
        if(!managedSimulator || (guiOwnership==null && MainFrame.get()!=null))throw new Fault("HARDWARE_UNQUALIFIED","Only an attested native simulator may mutate");
        if(guiOwnership!=null){guiOwnership.checkAttachment(config,machine);guiOwnership.requireNativeOwnership();}
        if(controllerDiagnostic!=null)controllerDiagnostic.guard(config);
        else {if(controllerJournal.hasHistory())throw new Fault("UNSUPPORTED_PROFILE_JOURNAL","Controller history requires its dedicated read-only recovery profile");verifyNativeSimulatorClasses(machine);if(portableLaunch!=null)portableLaunch.guard(config);}
        if("sustained-workload".equals(simulatorProfile)&&!"Unsorted".equals(nativeJobOrder()))throw new Fault("SIMULATOR_PROFILE_MISMATCH","The sustained preordered-grid fixture requires native Unsorted job order");
        if(journalFault)throw new Fault("JOURNAL_FAULT","Journal is unavailable");
        if(configurationFault)throw new Fault("CONFIGURATION_FAULT",topologyFault?"Nozzle assembly did not complete durably. Read the operation-linked topology_recovery_available event, retrieve its artifact with openpnp_get_native_artifact, and adopt that portable preimage into a fresh simulator generation and state directory. Restarting this state does not clear the topology fence.":"Configuration changed but did not complete; restart the isolated simulator before new mutations");
    }
    public static void verifyNativeSimulatorClasses(Machine machine) throws Fault {
        if(machine.getClass()!=ReferenceMachine.class || machine.getPnpJobProcessor().getClass()!=ReferencePnpJobProcessor.class)throw new Fault("HARDWARE_UNQUALIFIED","Only exact native ReferenceMachine and ReferencePnpJobProcessor are supported");
        if(machine.getDrivers().isEmpty())throw new Fault("HARDWARE_UNQUALIFIED","No verified simulator driver");
        for(Driver d:machine.getDrivers())checkClass(d);
        for(Camera c:machine.getAllCameras())checkClass(c);
        for(Feeder f:machine.getFeeders())checkClass(f);
        for(Actuator a:machine.getAllActuators())checkClass(a);
        for(Head h:machine.getHeads()){checkClass(h);for(Nozzle n:h.getNozzles())checkClass(n);}
    }
    private static void checkClass(Object o) throws Fault {if(!SIM_CLASSES.contains(o.getClass().getName()))throw new Fault("HARDWARE_UNQUALIFIED","Unqualified class: "+o.getClass().getName());}
    private synchronized void requireSession(JsonObject p) throws Exception {
        simulatorGuard();if(guiOwnership!=null)guiOwnership.requireRemoteGrant();checkLease();
        if(session==null||!session.equals(text(p,"session_id")))throw new Fault("SESSION_REQUIRED","Acquire an active simulator control session");
    }
    private synchronized void fence(long owner) throws Exception {
        simulatorGuard();checkLease();if(session==null||epoch!=owner)throw new Fault("OWNERSHIP_REVOKED","Operation ownership expired before dispatch");
    }
    private synchronized void checkLease() {
        if(session!=null&&System.nanoTime()>=sessionDeadline){session=null;epoch++;configurationPlans.clear();clearStructurePlans();pauseRequested=true;try{invalidateVacuumReadiness("ownership-change");event("session_expired",map("ownership_epoch",epoch));}catch(Exception e){journalFault=true;}}
    }
    private synchronized void requireIdle() throws Fault {
        requireVacuumNoFault();requireNoPendingSubmission();
        for(Map<String,Object> op:operations.values())if("outcome_unknown".equals(op.get("state"))&&!sensingReconciliation.liveDisposesOperation((String)op.get("operation_id"),instance))throw new Fault("RECOVERY_REQUIRED","Reconcile prior unknown operations before overlapping work");
        if(activeOperation!=null||machine.isBusy())throw new Fault("BUSY","An operation or native task is active");
    }
    private void requireEnabled() throws Fault {if(!machine.isEnabled())throw new Fault("MACHINE_DISABLED","Enable the simulator first");}
    private void requireHomed() throws Fault {requireEnabled();if(!machine.isHomed())throw new Fault("NOT_HOMED","Home the simulator first");}
    private synchronized void requireJobOperation(JsonObject p) throws Exception {if(activeOperation==null||!activeOperation.equals(text(p,"operation_id"))||job==null)throw new Fault("NOT_FOUND","No matching active job operation");}
    private synchronized Object sessionSnapshot(){return map("session_id",session,"ownership_epoch",epoch,"simulation_only",true,"expires_in_ms",session==null?0:Math.max(0,TimeUnit.NANOSECONDS.toMillis(sessionDeadline-System.nanoTime())));}
    private synchronized Object grantReceipt(String requestId){
        Map<String,Object> receipt=new LinkedHashMap<>(sessionReceipts.get(requestId));
        boolean active=Objects.equals(session,receipt.get("session_id"))&&System.nanoTime()<sessionDeadline;
        receipt.remove("request_digest");receipt.put("active",active);receipt.put("simulation_only",true);receipt.put("expires_in_ms",active?Math.max(0,TimeUnit.NANOSECONDS.toMillis(sessionDeadline-System.nanoTime())):0);
        receipt.put("lease_state",active?"active":"expired-or-revoked");return receipt;
    }
    private HeadMountable defaultTool()throws Exception{return machine.getDefaultHead().getDefaultCamera();}
    private HeadMountable tool(String id)throws Exception{for(Head h:machine.getHeads()){for(Nozzle n:h.getNozzles())if(n.getId().equals(id))return n;for(Camera c:h.getCameras())if(c.getId().equals(id))return c;}throw new Fault("NOT_FOUND","Unknown head-mounted tool");}
    private Camera camera(String id)throws Exception{Camera found=null;for(Camera c:machine.getAllCameras())if(c.getId().equals(id)){if(found!=null)throw new Fault("AMBIGUOUS_ID","Duplicate native camera ID");found=c;}if(found==null)throw new Fault("NOT_FOUND","Unknown camera");return found;}
    private Nozzle nozzle(String id)throws Exception{HeadMountable t=tool(id);if(!(t instanceof Nozzle))throw new Fault("INVALID_TOOL","A nozzle is required");return(Nozzle)t;}
    private synchronized String revisionString(){return "cfg-"+revision;}
    private synchronized Map<String,Object> operation(String id)throws Fault{Map<String,Object> result=operations.get(id);if(result==null)throw new Fault("NOT_FOUND","Unknown operation");Map<String,Object> copy=freezeDto(result);
        NativeSubmission submission=pendingSubmission;
        if(submission!=null&&id.equals(submission.operationId)){copy.put("native_completion",submissionSnapshot());if(submission.publicationFault!=null)copy.put("publication_fault",freezeDto(submission.publicationFault));}
        return copy;}
    private synchronized void transition(String id,String state,Object result)throws Exception{
        Map<String,Object> op=freezeDto(operations.get(id));
        if(id.equals(nativeLedgerOperation)&&nativeLedger!=null){lastNativeLedger=nativeLedger.snapshot();op.put("native_action_ledger",freezeDto(lastNativeLedger));}
        if(vacuumJournal.hasHistory())op.put("vacuum_sensing_journal",vacuumJournal.snapshot(instance));
        op.put("state",state);op.put("updated_at",Instant.now().toString());if(result!=null)op.put("result",freezeDto(result));
        if("accepted".equals(state)||"running".equals(state))op.remove("native_completion");
        NativeSubmission submission=submissionContext.get();
        if(submission!=null&&Set.of("paused","succeeded","failed","outcome_unknown","aborted","cancelled").contains(state)){
            if(!id.equals(submission.operationId)||submission.outcome!=null)throw new IllegalStateException("Native submission staged multiple or foreign outcomes");
            submission.outcome=op;return;
        }
        event("operation",op);operations.put(id,op);
    }
    private synchronized void finish(String id,String state,Object result)throws Exception{
        transition(id,state,result);NativeSubmission submission=submissionContext.get();
        if(submission==null)releaseOperation(id);else submission.releaseActive=true;
    }
    private void releaseOperation(String id){vacuumPlans.remove(id);if(id.equals(activeOperation))activeOperation=null;if(id.equals(nativeLedgerOperation)){nativeLedger=null;nativeLedgerOperation=null;}}
    /** Durable before the first assembly effect; recovery creates a separate disabled generation. */
    private Map<String,Object> topologyRecoveryPreimage()throws Exception {
        Path pending=journalDir.resolve(".topology-preimage-"+UUID.randomUUID()+".zip");
        try {
            NativePortableConfiguration.export(config,pending);
            byte[] bytes=NativePortableConfiguration.read(pending,MAX_REQUEST_BYTES);
            Map<String,Object> file=artifact("application/zip",bytes,map("kind","nozzle-assembly-recovery-preimage","scope","fresh-native-simulator-adoption","operation_id",activeOperation,"config_revision",revisionString(),"in_place_restore",false,"execution_authority_transferred",false));
            String id=(String)file.get("artifact_id");
            for(String suffix:List.of(".artifact",".metadata.json"))try(FileChannel channel=FileChannel.open(journalDir.resolve(id+suffix),StandardOpenOption.WRITE,LinkOption.NOFOLLOW_LINKS)){channel.force(true);}
            NativePortableConfiguration.forceDirectory(journalDir);
            Map<String,Object> op=operations.get(nativeOperationContext.get());NativeSubmission submission=submissionContext.get();
            if(op==null||submission==null||!Objects.equals(op.get("operation_id"),submission.operationId))throw new IOException("Topology preimage lacks native submission identity");
            Map<String,Object> binding=map("schema_version",1,"profile",NativeTopologyJournal.PROFILE,"machine_id",machineId,"submission_id",submission.token,"artifact",file);
            for(String key:List.of("operation_id","request_id","request_digest","method","bridge_instance_id","config_revision","accepted_at"))binding.put(key,op.get(key));
            event("topology_recovery_available",binding);
            return file;
        } finally {Files.deleteIfExists(pending);}
    }
    private synchronized Map<String,Object> artifact(String mime,byte[] data,Map<String,Object> metadata)throws Exception{
        if(data.length>MAX_REQUEST_BYTES)throw new Fault("ARTIFACT_TOO_LARGE","Artifact exceeds 8 MiB");
        if(artifactBytes>MAX_ARTIFACT_STORAGE_BYTES-data.length)throw new Fault("ARTIFACT_STORAGE_CAPACITY","Retained artifact content reached the 1 GiB admission limit; existing evidence is preserved");
        if(GSON.toJson(plain(metadata)).getBytes(StandardCharsets.UTF_8).length>60000)throw new Fault("ARTIFACT_METADATA_TOO_LARGE","Artifact metadata exceeds 60000 bytes");
        String id=UUID.randomUUID().toString();Path file=journalDir.resolve(id+".artifact");Files.write(file,data,StandardOpenOption.CREATE_NEW);artifactBytes+=data.length;artifactCount++;
        Map<String,Object> value=map("artifact_id",id,"mime_type",mime,"sha256",sha256(data),"size",data.length,"metadata",metadata);
        Files.writeString(journalDir.resolve(id+".metadata.json"),GSON.toJson(value),StandardOpenOption.CREATE_NEW);
        artifacts.put(id,value);while(artifacts.size()>MAX_ARTIFACT_METADATA)artifacts.remove(artifacts.keySet().iterator().next());return value;
    }
    private synchronized Map<String,Object> jobSummary(){
        if(job==null)return map("job_id",null,"state","absent");
        ArrayList<Map<String,Object>> placements=new ArrayList<>();int placed=0;
        for(BoardLocation board:job.getBoardLocations())if(board.isEnabled())for(Placement p:board.getBoard().getPlacements()){
            if(!p.isEnabled()||p.getType()!=Placement.Type.Placement||p.getSide()!=board.getGlobalSide())continue;
            boolean done=job.retrievePlacedStatus(board,p.getId());if(done)placed++;
            placements.add(map("board_id",board.getUniqueId(),"reference",p.getId(),"part_id",p.getPart()==null?null:p.getPart().getId(),"placed",done,"independently_inspected",false,"machine_pose",pose(Utils2D.calculateBoardPlacementLocation(board,p))));
        }
        return map("job_lineage",lineageReadback(),"job_id",jobId,"job_revision",boardLoads.jobRevision(),"board_load_revision",boardLoads.revision(),"board_loads",boardLoads.snapshot(),"state",bodyJobState(),"requested",placements.size(),"placed",placed,"independently_inspected",0,"placements",placements);
    }
    private synchronized void replaceJob(Job next){
        if(job!=null)job.removePropertyChangeListener(jobCountListener);
        clearStructurePlans();job=next;jobCountsDirty=true;validatedBoardLoadRevision=null;
        if(job!=null)job.addPropertyChangeListener(jobCountListener);
    }
    private void publishGuiJob() throws Exception {if(guiOwnership!=null && job!=null)guiOwnership.publishJob(job);}
    private synchronized Map<String,Object> jobCounts(){
        if(job==null)return map("job_id",null,"state","absent");
        if(jobCountsDirty){
            int requested=0,placed=0;
            for(BoardLocation board:job.getBoardLocations())if(board.isEnabled())for(Placement p:board.getBoard().getPlacements())if(p.isEnabled()&&p.getType()==Placement.Type.Placement&&p.getSide()==board.getGlobalSide()){requested++;if(job.retrievePlacedStatus(board,p.getId()))placed++;}
            cachedRequested=requested;cachedPlaced=placed;jobCountsDirty=false;jobCountRecomputations++;
        }
        return map("job_id",jobId,"state",bodyJobState(),"requested",cachedRequested,"placed",cachedPlaced,"independently_inspected",0);
    }
    /** Combine the historical launch snapshot with the latest immutable diagnostic DTO.
     * This read uses no Configuration, Machine or native-owner accessors. */
    private synchronized Map<String,Object> configurationSnapshot(){
        if(controllerDiagnostic==null)return snapshot;
        Map<String,Object> observed=new LinkedHashMap<>(snapshot);
        observed.put("controller_diagnostic",controllerDiagnostic.cached());
        observed.put("configuration_snapshot_scope","initial-empty-controller-shell");
        observed.put("controller_diagnostic_source","latest-cached-diagnostic-observation");
        observed.put("native_model_read_performed",false);
        return observed;
    }
    private synchronized void refresh()throws Exception{
        if(controllerDiagnostic!=null){snapshot=map("snapshot_at",Instant.now().toString(),"config_revision",revisionString(),"profile",NativeControllerJournal.PROFILE,"controller_diagnostic",controllerDiagnostic.cached(),"settings",Collections.emptyMap(),"position_source","unavailable","physical_qualification",false);jobProgress=map("state","absent");return;}

        ArrayList<Object> drivers=new ArrayList<>(),cameras=new ArrayList<>(),feeders=new ArrayList<>(),parts=new ArrayList<>(),tools=new ArrayList<>(),actuators=new ArrayList<>();
        for(Driver d:machine.getDrivers())drivers.add(map("id",d.getId(),"name",d.getName(),"class",d.getClass().getName()));
        for(Camera c:machine.getAllCameras())cameras.add(map("id",c.getId(),"name",c.getName(),"class",c.getClass().getName(),"looking",c.getLooking().toString()));
        for(Head h:machine.getHeads())for(Nozzle n:h.getNozzles())tools.add(map("id",n.getId(),"name",n.getName(),"pose",pose(n.getLocation()),"nozzle_tip_id",n.getNozzleTip()==null?null:n.getNozzleTip().getId()));
        for(Feeder f:machine.getFeeders()){
            Map<String,Object> feeder=map("id",f.getId(),"name",f.getName(),"class",f.getClass().getName(),"enabled",f.isEnabled(),"part_id",f.getPart()==null?null:f.getPart().getId());
            if(f instanceof ReferenceTrayFeeder){ReferenceTrayFeeder tray=(ReferenceTrayFeeder)f;feeder.put("feed_count",tray.getFeedCount());feeder.put("capacity",tray.getEffectiveTrayCountX()*tray.getEffectiveTrayCountY());feeder.put("virtual_supply","sustained-workload".equals(simulatorProfile));}
            feeders.add(feeder);
        }
        for(Part p:config.getParts())parts.add(map("id",p.getId(),"name",p.getName(),"height_mm",p.getHeight().convertToUnits(LengthUnit.Millimeters).getValue(),"package_id",p.getPackage()==null?null:p.getPackage().getId()));
        for(Actuator a:machine.getAllActuators())actuators.add(map("id",a.getId(),"name",a.getName(),"last_command",a.getLastActuationValue(),"feedback_provenance","cached-command"));
        if(boardLoads!=null)boardLoads.publishSnapshot();
        if(materialLoads!=null&&machine.isTask(Thread.currentThread()))materialLoads.observeConfiguration();
        Map<String,Object> progress=jobCounts();progress.put("observed_at",Instant.now().toString());progress.put("through_sequence",sequence);NativeSubmission current=submissionContext.get();if(current!=null){current.progress=freezeDto(progress);progress.put("state",jobState);}jobProgress=progress;
        machineSnapshotRefreshes++;lastSnapshotNanos=System.nanoTime();
        try{vacuumSources=NativeVacuumSources.inspect(config);}catch(LinkageError unavailable){vacuumSources=map("profile",NativeVacuumSensing.PROFILE,"available",false,"source_profile","controlled-native-vacuum-v1","simulation_only",true,"hardware_qualified",false,"reason","native-api-unavailable");}
        snapshot=map("snapshot_at",Instant.now().toString(),"config_revision",revisionString(),"enabled",machine.isEnabled(),"homed",machine.isHomed(),"speed",machine.getSpeed(),"drivers",drivers,"cameras",cameras,"nozzles",tools,"feeders",feeders,"parts",parts,"actuators",actuators,"settings",NativeSettings.describe(config),"position_source","native-simulation");
    }
    private static boolean observesLineage(String type){return type.startsWith("job_lineage_")||Set.of("operation","board_load_intent","board_load_outcome","board_load_history","board_replacement_intent","board_replacement_outcome","board_continuation_intent","board_continuation_outcome","native_placement_checkpoint").contains(type);}
    private static boolean recoveryDependencyRecord(String type){return type.startsWith("faulted_job_replacement_")||type.startsWith("material_replacement_")||type.startsWith("board_replacement_")||type.equals("job_lineage_replacement")||NativeFaultedJobReplacement.isContinuationEvent(type)||NativeFaultedJobReplacement.isRestartObservation(type);}
    private synchronized void event(String type,Object payload)throws Exception{event(type,payload,true);}
    private synchronized void event(String type,Object payload,boolean observeLineage)throws Exception{
        if(journalFault)throw new IOException("Journal previously failed");
        if(NativeVacuumJournal.matches(type))validateVacuumRecord(type,(Map<String,Object>)payload,instance,false);
        Runnable vacuumCommit=NativeVacuumJournal.matches(type)?vacuumJournal.prepare(type,(Map<String,Object>)payload,instance):null;
        Runnable sensingRecoveryCommit=NativeSensingReconciliation.matches(type)?sensingReconciliation.prepare(type,(Map<String,Object>)payload,instance):null;
        Runnable topologyCommit=NativeTopologyJournal.matches(type)?topologyJournal.prepare(type,(Map<String,Object>)payload,instance):null;
        Runnable inspectionCommit=NativeInspectionJournal.matches(type)?inspectionJournal.prepare(type,(Map<String,Object>)plain(payload),instance):null;
        Runnable controllerCommit=NativeControllerJournal.matches(type)?controllerJournal.prepare(type,(Map<String,Object>)plain(payload),operations):null;
        Runnable lineageCommit=observeLineage&&jobLineage!=null&&observesLineage(type)?jobLineage.prepareObservation(type,(Map<String,Object>)plain(payload)):null;
        Runnable materialCommit=materialLoads==null?null:materialLoads.prepareEvent(type,(Map<String,Object>)plain(payload));
        Runnable continuationCommit=faultedJobReplacement==null?null:faultedJobReplacement.prepareContinuationObservation(type,(Map<String,Object>)plain(payload));
        Map<String,Object> event=map("sequence",sequence+1,"bridge_instance_id",instance,"occurred_at",Instant.now().toString(),"type",type,"payload",payload);
        byte[] bytes=((NativeVacuumJournal.matches(type)||NativeSensingReconciliation.matches(type)||recoveryDependencyRecord(type)?VACUUM_JSON:GSON).toJson(plain(event))+"\n").getBytes(StandardCharsets.UTF_8);
        Map<String,Object> durableEvent=NativeJournalJson.parseObject(new String(bytes,StandardCharsets.UTF_8));
        if(bytes.length>MAX_JOURNAL_RECORD_BYTES){journalFault=true;pauseRequested=true;throw new Fault("JOURNAL_RECORD_CAPACITY","Durable record exceeds the bounded recovery size");}
        if(journal.size()+bytes.length>MAX_JOURNAL_BYTES){journalFault=true;pauseRequested=true;throw new Fault("JOURNAL_CAPACITY","Journal capacity reached; preserve and export state before starting a new journal");}
        try{ByteBuffer buffer=ByteBuffer.wrap(bytes);while(buffer.hasRemaining())journal.write(buffer);journal.force(true);if(materialCommit!=null)materialCommit.run();if(lineageCommit!=null)lineageCommit.run();if(continuationCommit!=null)continuationCommit.run();if(controllerCommit!=null)controllerCommit.run();if(topologyCommit!=null)topologyCommit.run();if(inspectionCommit!=null)inspectionCommit.run();if(vacuumCommit!=null)vacuumCommit.run();if(sensingRecoveryCommit!=null)sensingRecoveryCommit.run();if(faultedJobReplacement!=null&&(type.equals("operation")||NativeActionLedger.isLedgerEventType(type)))faultedJobReplacement.observe(type,(Map<String,Object>)durableEvent.get("payload"));}catch(Exception|Error e){journalFault=true;pauseRequested=true;if(jobLineage!=null)jobLineage.publicationFailed();throw e;}
        sequence++;events.add(durableEvent);if(events.size()>5000)events.remove(0);
        if(boardLoads!=null && "native_placement_checkpoint".equals(type))boardLoads.observeNativeEvent(type,(Map<String,Object>)plain(payload));
    }
    private synchronized void requireRequestCapacity()throws Fault {if(requests.size()>=MAX_REQUESTS)throw new Fault("REQUEST_CAPACITY","Durable receipt capacity reached; preserve this journal and start a new isolated simulator state");}
    private synchronized void expirePlans(){long now=System.nanoTime();for(String id:new ArrayList<>(plans.keySet()))if(now>=planDeadlines.getOrDefault(id,0L)){plans.remove(id);planDeadlines.remove(id);placementPlans.remove(id);discardStructurePlan(id);configurationPlans.remove(id);}}
    private synchronized String storePlan(Map<String,Object> plan){expirePlans();while(plans.size()>=MAX_PLANS){String oldest=plans.keySet().iterator().next();plans.remove(oldest);planDeadlines.remove(oldest);placementPlans.remove(oldest);discardStructurePlan(oldest);configurationPlans.remove(oldest);}String id=UUID.randomUUID().toString();plans.put(id,plan);planDeadlines.put(id,System.nanoTime()+PLAN_TTL_NANOS);return id;}
    private synchronized Object metrics()throws IOException {expirePlans();Runtime runtime=Runtime.getRuntime();return map("uptime_ms",TimeUnit.NANOSECONDS.toMillis(System.nanoTime()-startedNanos),"heap_used_bytes",runtime.totalMemory()-runtime.freeMemory(),"heap_committed_bytes",runtime.totalMemory(),"heap_max_bytes",runtime.maxMemory(),"event_buffer_count",events.size(),"operation_count",operations.size(),"request_count",requests.size(),"plan_count",plans.size(),"artifact_count",artifactCount,"artifact_metadata_cache_count",artifacts.size(),"artifact_bytes",artifactBytes,"artifact_memory_content_bytes",0,"journal_bytes",journalSizeForStatus(),"job_count_recomputations",jobCountRecomputations,"machine_snapshot_refreshes",machineSnapshotRefreshes);}
    private Long journalSizeForStatus(){try{return journal.size();}catch(IOException unavailable){return null;}}
    private void recoverJournal(Path file)throws Exception{
        if(Files.size(file)>MAX_JOURNAL_BYTES)throw new IOException("Journal exceeds bounded recovery capacity");
        Map<String,NativeActionLedger.Replay> actionReplay=new HashMap<>();
        try(java.io.InputStream reader=new java.io.BufferedInputStream(Files.newInputStream(file))) {byte[] line;while((line=readJournalRecord(reader))!=null){
            try{
                checkJsonDepth(line);
                String record=StandardCharsets.UTF_8.newDecoder().onMalformedInput(java.nio.charset.CodingErrorAction.REPORT).onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT).decode(ByteBuffer.wrap(line)).toString();
                if(record.isBlank())continue;
                Map<String,Object> e=NativeJournalJson.parseObject(record);
                if(NativeSensingReconciliation.matches(String.valueOf(e.get("type"))))sensingReconciliation.recover((String)e.get("type"),(Map<String,Object>)e.get("payload"),(String)e.get("bridge_instance_id"));
                if(NativeInspectionJournal.matches(String.valueOf(e.get("type")))){NativeInspectionJournal.validateRawRecord(record);inspectionJournal.recover((String)e.get("type"),(Map<String,Object>)e.get("payload"),(String)e.get("bridge_instance_id"));}
                if(NativeVacuumJournal.matches(String.valueOf(e.get("type")))){NativeVacuumJournal.validateRawRecord(record);validateVacuumRecord((String)e.get("type"),(Map<String,Object>)e.get("payload"),(String)e.get("bridge_instance_id"),true);vacuumJournal.recover((String)e.get("type"),(Map<String,Object>)e.get("payload"),(String)e.get("bridge_instance_id"));}
                long recorded=NativeJournalJson.integer(e.get("sequence"),1,9007199254740991L);if(recorded!=sequence+1)throw new IOException("Journal sequence is not contiguous");sequence++;
                topologyJournal.recover(String.valueOf(e.get("type")),(Map<String,Object>)e.get("payload"),(String)e.get("bridge_instance_id"));
                if(NativeActionLedger.isLedgerEventType(String.valueOf(e.get("type")))){
                    Map<String,Object> payload=(Map<String,Object>)e.get("payload");String operationId=(String)payload.get("operation_id");
                    actionReplay.computeIfAbsent(operationId,NativeActionLedger.Replay::new).accept(e);
                }
                if(NativeControllerJournal.matches(String.valueOf(e.get("type"))))controllerJournal.acceptRecovered(String.valueOf(e.get("type")),(Map<String,Object>)e.get("payload"),operations);
                Runnable continuationReplayCommit=faultedJobReplacement.prepareContinuationObservation((String)e.get("type"),(Map<String,Object>)e.get("payload"));
                materialLoads.recoverEvent(String.valueOf(e.get("type")),(Map<String,Object>)e.get("payload"));
                boardLoads.recoverEvent(String.valueOf(e.get("type")),(Map<String,Object>)e.get("payload"));boardLoads.observeNativeEvent(String.valueOf(e.get("type")),(Map<String,Object>)e.get("payload"));
                if(observesLineage(String.valueOf(e.get("type"))))jobLineage.observe(String.valueOf(e.get("type")),(Map<String,Object>)e.get("payload"));
                if(String.valueOf(e.get("type")).startsWith("faulted_job_replacement_"))faultedJobReplacement.recover((String)e.get("type"),(Map<String,Object>)e.get("payload"));
                else if("operation".equals(e.get("type"))||NativeActionLedger.isLedgerEventType((String)e.get("type")))faultedJobReplacement.observe((String)e.get("type"),(Map<String,Object>)e.get("payload"));
                continuationReplayCommit.run();
                events.add(e);if(events.size()>5000)events.remove(0);
                if("operation".equals(e.get("type"))){Map<String,Object> op=new LinkedHashMap<>((Map<String,Object>)e.get("payload"));String id=(String)op.get("operation_id");controllerJournal.observeRecoveredOperation(op,operations.get(id));operations.put(id,op);requests.put((String)op.get("request_id"),id);requestDigests.put((String)op.get("request_id"),(String)op.get("request_digest"));}
                if("command_receipt".equals(e.get("type"))){Map<String,Object> receipt=new LinkedHashMap<>((Map<String,Object>)e.get("payload"));String requestId=(String)receipt.get("request_id");commandReceipts.put(requestId,receipt);requests.put(requestId,(String)receipt.get("operation_id"));requestDigests.put(requestId,(String)receipt.get("request_digest"));}
                if("session_receipt".equals(e.get("type"))){Map<String,Object> receipt=new LinkedHashMap<>((Map<String,Object>)e.get("payload"));String requestId=(String)receipt.get("request_id");sessionReceipts.put(requestId,receipt);requests.put(requestId,"session:"+requestId);requestDigests.put(requestId,(String)receipt.get("request_digest"));}
                if(requests.size()>MAX_REQUESTS)throw new IOException("Journal contains more than the bounded request capacity");
            }catch(Exception e){throw new IOException("Journal is malformed; preserve it for recovery",e);}
        }}
        topologyJournal.verifyMachineIdentity(machineId);
        inspectionJournal.verifyMachineIdentity(machineId);vacuumJournal.verifyMachineIdentity(machineId);
        if(controllerJournal.hasHistory()){Map<String,Object> op=operations.get(controllerJournal.operationId());if(op==null)throw new IOException("Controller history lost original operation");controllerJournal.finishRecovery(op);op.put("controller_recovery",controllerJournal.snapshot());}
        for(Map.Entry<String,NativeActionLedger.Replay> recovered:actionReplay.entrySet()){
            Map<String,Object> op=operations.get(recovered.getKey());if(op==null)throw new IOException("Native action journal has no operation admission");
            Map<String,Object> facts=recovered.getValue().snapshot();op.put("native_action_recovery",facts);
            Object rawDisposition=op.get("result");Map<?,?> disposition=rawDisposition instanceof Map?(Map<?,?>)rawDisposition:Collections.emptyMap();
            boolean abandoned="cancelled".equals(op.get("state"))&&"abandoned-after-simulator-reset".equals(disposition.get("resolution"))&&"unknown".equals(disposition.get("previous_physical_outcome"))&&Boolean.FALSE.equals(disposition.get("repeat_action_performed"));
            if(Boolean.TRUE.equals(facts.get("requires_reconciliation"))&&!abandoned&&!"outcome_unknown".equals(op.get("state")))transition(recovered.getKey(),"outcome_unknown",map("recovery","native-action-outcome-unknown","repeat_action_performed",false));
        }
        for(Map<String,Object> op:operations.values())if(Arrays.asList("accepted","running","paused").contains(op.get("state")))transition((String)op.get("operation_id"),"outcome_unknown",map("recovery","prior-instance-interrupted","repeat_action_performed",false));
        // A process restart cannot turn a partial topology write into a reusable configuration.
        // Read-only receipts/artifacts remain available; recover into a fresh portable generation.
        if(topologyJournal.recoveryRequired()){topologyFault=true;configurationFault=true;}
        for(Map<String,Object> receipt:commandReceipts.values())if("accepted".equals(receipt.get("state"))){receipt.put("state","outcome_unknown");event("command_receipt",receipt);Map<String,Object> op=operations.get(receipt.get("operation_id"));if(op!=null&&!"outcome_unknown".equals(op.get("state")))transition((String)op.get("operation_id"),"outcome_unknown",map("recovery","command-outcome-unknown","repeat_action_performed",false));}
    }
    private static byte[] readJournalRecord(java.io.InputStream input)throws IOException {
        ByteArrayOutputStream record=new ByteArrayOutputStream();int value;
        while((value=input.read())!=-1){
            if(value=='\n')return record.toByteArray();
            if(record.size()>=MAX_JOURNAL_RECORD_BYTES)throw new IOException("Journal record exceeds bounded recovery size");
            record.write(value);
        }
        if(record.size()!=0)throw new IOException("Journal ended inside an uncommitted record; preserve for recovery");
        return null;
    }
    @Override public synchronized void close()throws Exception{if(closed)return;drainNativeCompletion();if(!explicitUnknownExit)requireNoPendingSubmission();sensingReconciliation.revokeAll();if(pendingSensingReconciliation!=null){PendingSensingReconciliation recovery=pendingSensingReconciliation;closeSensingReconciliation(recovery,"cancelled");if(recovery.completion!=null&&!recovery.completion.isDone())recovery.completion.completeExceptionally(new IllegalStateException("Bridge closed"));}if(pendingInspection!=null){closeInspection(pendingInspection,"ownership-revoked");if(pendingInspection.completion!=null&&!pendingInspection.completion.isDone())pendingInspection.completion.completeExceptionally(new IllegalStateException("Bridge closed"));}closed=true;for(CompletableFuture<Map<String,Object>> completion:inspectionCompletions)completion.completeExceptionally(new IllegalStateException("Bridge closed"));session=null;epoch++;configurationPlans.clear();clearStructurePlans();pauseRequested=true;server.stop(0);watchdog.shutdownNow();httpThreads.shutdownNow();machine.getPnpJobProcessor().removeTextStatusListener(statusListener);if(job!=null)job.removePropertyChangeListener(jobCountListener);if(pendingSubmission==null)faultedJobReplacement.close();journalLock.release();journal.close();}

    private static Map<String,Object> pose(Location location){Location p=location.convertToUnits(LengthUnit.Millimeters);return map("x",p.getX(),"y",p.getY(),"z",p.getZ(),"rotation",p.getRotation(),"units","mm");}
    public static Path copySample(Path sampleRoot,Path workspace)throws Exception {
        Path source=sampleRoot.resolve("pnp-test").toRealPath();if(!source.startsWith(sampleRoot.toRealPath()))throw new Fault("PATH_REJECTED","Sample outside approved root");
        Path jobs=workspace.resolve("jobs");Files.createDirectories(jobs);Path destination=Files.createTempDirectory(jobs,"sample-");
        try(java.util.stream.Stream<Path> files=Files.walk(source)){for(Path file:(Iterable<Path>)files::iterator){if(Files.isSymbolicLink(file))throw new Fault("PATH_REJECTED","Sample symlinks are unsupported");Path target=destination.resolve(source.relativize(file));if(Files.isDirectory(file))Files.createDirectories(target);else if(Files.isRegularFile(file))Files.copy(file,target);}}
        return destination;
    }
    public static Map<String,Object> map(Object...pairs){Map<String,Object> m=new LinkedHashMap<>();for(int i=0;i<pairs.length;i+=2)m.put((String)pairs[i],pairs[i+1]);return m;}
    // Old upstream Gson tries reflective construction of JDK-private empty collection classes.
    // Normalize our typed DTO collections rather than opening java.util to reflection.
    private static Object plain(Object value){if(value instanceof Map){Map<String,Object> result=new LinkedHashMap<>();for(Map.Entry<?,?> entry:((Map<?,?>)value).entrySet())result.put((String)entry.getKey(),plain(entry.getValue()));return result;}if(value instanceof Iterable){List<Object> result=new ArrayList<>();for(Object item:(Iterable<?>)value)result.add(plain(item));return result;}return value;}
    private static void only(JsonObject p,String...keys)throws Fault{Set<String> allowed=new HashSet<>(Arrays.asList(keys));for(Map.Entry<String,JsonElement> e:p.entrySet())if(!allowed.contains(e.getKey()))throw new Fault("UNKNOWN_FIELD","Unknown field: "+e.getKey());}
    private static String text(JsonObject p,String key)throws Fault{if(!p.has(key)||!p.get(key).isJsonPrimitive()||!p.getAsJsonPrimitive(key).isString()||p.get(key).getAsString().length()>1024||p.get(key).getAsString().isEmpty())throw new Fault("INVALID_ARGUMENT","Expected nonempty string: "+key);return p.get(key).getAsString();}
    private static String optionalText(JsonObject p,String key,String fallback)throws Fault{return p.has(key)?text(p,key):fallback;}
    private static boolean bool(JsonObject p,String key)throws Fault{if(!p.has(key)||!p.get(key).isJsonPrimitive()||!p.getAsJsonPrimitive(key).isBoolean())throw new Fault("INVALID_ARGUMENT","Expected boolean: "+key);return p.get(key).getAsBoolean();}
    private static double number(JsonObject p,String key,double min,double max)throws Fault{if(!p.has(key)||!p.get(key).isJsonPrimitive()||!p.getAsJsonPrimitive(key).isNumber())throw new Fault("INVALID_ARGUMENT","Expected number: "+key);double v=p.get(key).getAsDouble();if(!Double.isFinite(v)||v<min||v>max)throw new Fault("OUT_OF_RANGE",key+" out of range");return v;}
    private static double optionalNumber(JsonObject p,String key,double fallback,double min,double max)throws Fault{return p.has(key)?number(p,key,min,max):fallback;}
    private static long integer(JsonObject p,String key,long fallback,long min,long max)throws Fault{if(!p.has(key))return fallback;double d=number(p,key,min,max);if(d!=Math.floor(d))throw new Fault("INVALID_ARGUMENT","Expected integer: "+key);return(long)d;}
    private static String canonical(JsonElement e){if(e.isJsonObject()){TreeMap<String,String> m=new TreeMap<>();for(Map.Entry<String,JsonElement> entry:e.getAsJsonObject().entrySet())m.put(entry.getKey(),canonical(entry.getValue()));return GSON.toJson(m);}return e.toString();}
    private static String sha256(String s)throws Exception{return sha256(s.getBytes(StandardCharsets.UTF_8));}
    private static String loadedBridgeSha256(){try{java.security.CodeSource source=Bridge.class.getProtectionDomain().getCodeSource();if(source==null)return null;Path file=java.nio.file.Paths.get(source.getLocation().toURI());if(!Files.isRegularFile(file)||!file.getFileName().toString().endsWith(".jar"))return null;return sha256(Files.readAllBytes(file));}catch(Exception e){return null;}}
    private static String sha256(byte[] data)throws Exception{StringBuilder b=new StringBuilder();for(byte v:MessageDigest.getInstance("SHA-256").digest(data))b.append(String.format("%02x",v));return b.toString();}
    private static String safeMessage(Throwable e){String s=e.getMessage();return s==null?e.getClass().getSimpleName():s.substring(0,Math.min(s.length(),2000));}
    private static void checkJsonDepth(byte[] body)throws Fault{boolean quoted=false,escaped=false;int depth=0;for(byte b:body){if(quoted){if(escaped)escaped=false;else if(b=='\\')escaped=true;else if(b=='"')quoted=false;}else if(b=='"')quoted=true;else if(b=='{'||b=='['){if(++depth>64)throw new Fault("INPUT_TOO_DEEP","JSON nesting exceeds 64 levels");}else if(b=='}'||b==']')depth--;}}
    public static final class Fault extends Exception{public final String code;public final Object details;public Fault(String code,String message){this(code,message,null);}public Fault(String code,String message,Object details){super(message);this.code=code;this.details=details;}}
}
