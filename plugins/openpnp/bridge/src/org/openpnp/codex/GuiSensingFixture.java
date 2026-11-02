/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import com.google.gson.Gson;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.util.*;
import javax.swing.SwingUtilities;
import org.openpnp.model.Configuration;
import org.openpnp.machine.reference.driver.NullDriver;

/** Creates an explicit GUI fixture before Main starts; binds its source only in the GUI JVM. */
public final class GuiSensingFixture {
    static final String PROFILE = "prepared-native-gui-vacuum-fixture-v1";
    private static final int MAX_FILES = 256, MAX_FILE_BYTES = 8 * 1024 * 1024, MAX_TOTAL_BYTES = 32 * 1024 * 1024;
    private GuiSensingFixture() { }

    public static void main(String[] args) throws Exception {
        Map<String,String> options = new LinkedHashMap<>();
        for (int i=0;i<args.length;i+=2) {
            if (i+1>=args.length || !Set.of("--config-dir","--manifest","--scenario").contains(args[i]) || options.put(args[i],args[i+1])!=null)
                throw new IllegalArgumentException("Expected unique --config-dir, --manifest and --scenario pairs");
        }
        if (!options.keySet().equals(Set.of("--config-dir","--manifest","--scenario"))) throw new IllegalArgumentException("All fixture arguments are required");
        Path config = Path.of(options.get("--config-dir")), manifest = Path.of(options.get("--manifest"));
        if (!config.isAbsolute() || !manifest.isAbsolute()) throw new IllegalArgumentException("Absolute fixture paths required");
        try {
            Map<String,Object> result = prepare(config,manifest,options.get("--scenario"));
            System.out.println(new Gson().toJson(result));
        } finally {
            try { if (Configuration.get()!=null && Configuration.get().getMachine()!=null) Configuration.get().getMachine().close(); }
            catch (Exception cleanup) { throw new IOException("Prepared simulator did not close cleanly",cleanup); }
        }
    }

    /** The caller must select a new private directory; no existing machine files are accepted. */
    static Map<String,Object> prepare(Path directory, Path manifest, String scenario) throws Exception {
        if (!NativeVacuumSources.SCENARIOS.contains(scenario)) throw new IllegalArgumentException("Unknown fixed sensing scenario");
        directory = directory.toAbsolutePath().normalize(); manifest = manifest.toAbsolutePath().normalize();
        if (manifest.startsWith(directory) || Files.exists(manifest,LinkOption.NOFOLLOW_LINKS)) throw new IOException("Use a new manifest outside the configuration directory");
        Files.createDirectories(directory);
        if (Files.isSymbolicLink(directory) || !directory.toRealPath().equals(directory)) throw new IOException("Fixture directory cannot contain symbolic links");
        try (java.util.stream.Stream<Path> paths = Files.walk(directory)) {
            if (paths.anyMatch(p -> Files.isSymbolicLink(p) || !Files.isDirectory(p,LinkOption.NOFOLLOW_LINKS))) throw new IOException("Fixture configuration must contain no files");
        }
        Configuration.initialize(directory.toFile()); Configuration.get().load();
        SimulatorMain.accelerateFixture(Configuration.get()); SimulatorMain.settleFreshFixture(directory);
        SimulatorMain.configureVacuumSensingFixture(Configuration.get()); SimulatorMain.settleFreshFixture(directory);
        // The preparation JVM creates settings only. Source authority cannot cross JVMs.
        Map<String,Object> record = new LinkedHashMap<>();
        record.put("schema_version",1); record.put("profile",PROFILE); record.put("upstream_commit",Bridge.UPSTREAM);
        record.put("config_directory",directory.toString()); record.put("scenario",scenario);
        record.put("files",inventory(directory)); record.put("source_authority_created",false);
        record.put("simulation_only",true); record.put("hardware_qualified",false);
        byte[] bytes = (new Gson().toJson(record)+"\n").getBytes(StandardCharsets.UTF_8);
        Files.createDirectories(manifest.getParent());
        try (FileChannel channel = FileChannel.open(manifest,Set.of(StandardOpenOption.CREATE_NEW,StandardOpenOption.WRITE),
                PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))) {
            ByteBuffer buffer = ByteBuffer.wrap(bytes); while(buffer.hasRemaining()) channel.write(buffer); channel.force(true);
        }
        try (FileChannel parent = FileChannel.open(manifest.getParent(),StandardOpenOption.READ)) { parent.force(true); }
        return Map.of("profile",PROFILE,"manifest",manifest.toString(),"manifest_sha256",hash(bytes),"scenario",scenario,
            "source_authority_created",false,"simulation_only",true,"hardware_qualified",false);
    }

    /** Called on EDT after Main has loaded the generated settings. Never replaces Configuration. */
    static Map<String,Object> claimPreparedGuiFixture(Configuration config, Path manifest, String expectedHash, String scenario) throws Exception {
        if (!SwingUtilities.isEventDispatchThread()) throw new IllegalStateException("GUI fixture claim requires EDT");
        if (config==null || config!=Configuration.get() || config.getMachine()==null || config.getMachine().isEnabled()
                || config.getMachine().isHomed() || config.getMachine().isBusy()) throw new IOException("Current disabled, unhomed, idle GUI configuration required");
        if (expectedHash==null || !expectedHash.matches("[a-f0-9]{64}") || !NativeVacuumSources.SCENARIOS.contains(scenario)) throw new IOException("Invalid prepared fixture identity");
        if (!Files.isRegularFile(manifest,LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(manifest) || Files.size(manifest)>1024*1024) throw new IOException("Prepared fixture manifest is not a bounded regular file");
        byte[] bytes=Files.readAllBytes(manifest); if(!expectedHash.equals(hash(bytes))) throw new IOException("Prepared fixture manifest changed");
        Map<String,Object> record=NativeJournalJson.parseObject(new String(bytes,StandardCharsets.UTF_8));
        if (!record.keySet().equals(Set.of("schema_version","profile","upstream_commit","config_directory","scenario","files","source_authority_created","simulation_only","hardware_qualified"))
                || NativeJournalJson.integer(record.get("schema_version"),1,1)!=1 || !PROFILE.equals(record.get("profile"))
                || !Bridge.UPSTREAM.equals(record.get("upstream_commit")) || !scenario.equals(record.get("scenario"))
                || !Boolean.FALSE.equals(record.get("source_authority_created")) || !Boolean.TRUE.equals(record.get("simulation_only"))
                || !Boolean.FALSE.equals(record.get("hardware_qualified"))) throw new IOException("Unsupported prepared fixture manifest");
        Path directory=config.getConfigurationDirectory().toPath().toAbsolutePath().normalize();
        if (!directory.toString().equals(record.get("config_directory")) || Files.isSymbolicLink(directory) || !directory.toRealPath().equals(directory)) throw new IOException("GUI loaded a different configuration directory");
        List<Map<String,Object>> current=inventory(directory);
        validateInventory(current,record.get("files"));
        Bridge.verifyNativeSimulatorClasses(config.getMachine()); NativeVacuumSources.requireBoundedMachine(config);
        NativeVacuumSources.requireLifecycleSafe(config);
        if(config.getMachine().getDrivers().size()!=1 || config.getMachine().getDrivers().get(0).getClass()!=NullDriver.class)
            throw new IOException("One exact simulator driver required");
        if(((NullDriver)config.getMachine().getDrivers().get(0)).getControlledVacuumSource()!=null)
            throw new IOException("A prepared GUI claim cannot replace an existing controlled source");
        Prepared claim=new Prepared(config,scenario);
        SimulatorMain.installPreparedGuiSource(config,scenario,claim);
        Map<String,Object> result=new LinkedHashMap<>(); result.put("profile",PROFILE);result.put("manifest_sha256",expectedHash);
        result.put("scenario",scenario);result.put("sensing_fixture_attested",true);result.put("configuration_replaced",false);
        result.put("source_authority","fresh-current-GUI-process");result.put("vacuum_sensing",NativeVacuumSources.inspect(config));
        result.put("simulation_only",true);result.put("hardware_qualified",false);return Collections.unmodifiableMap(result);
    }

    /** Read-only local capture. The prepared manifest is provenance; current saved files are
     * deliberately captured anew after legitimate native saves. This creates no source. */
    static RestartAttestation captureRestart(Configuration config, Path manifest, String expectedHash, String scenario) throws Exception {
        if (!SwingUtilities.isEventDispatchThread()) throw new IllegalStateException("Restart fixture capture requires EDT");
        requireRestartConfiguration(config,false);
        if(expectedHash==null||!expectedHash.matches("[a-f0-9]{64}")||!NativeVacuumSources.SCENARIOS.contains(scenario))throw new IOException("Invalid original prepared fixture identity");
        manifest=manifest.toAbsolutePath().normalize();
        if(!Files.isRegularFile(manifest,LinkOption.NOFOLLOW_LINKS)||Files.isSymbolicLink(manifest)||!manifest.toRealPath().equals(manifest)||Files.size(manifest)>1024*1024)throw new IOException("Original prepared manifest must be a bounded regular file");
        byte[] bytes=Files.readAllBytes(manifest);if(!expectedHash.equals(hash(bytes)))throw new IOException("Original prepared manifest changed");
        Map<String,Object> record=NativeJournalJson.parseObject(new String(bytes,StandardCharsets.UTF_8));
        if(!record.keySet().equals(Set.of("schema_version","profile","upstream_commit","config_directory","scenario","files","source_authority_created","simulation_only","hardware_qualified"))
                ||NativeJournalJson.integer(record.get("schema_version"),1,1)!=1||!PROFILE.equals(record.get("profile"))||!Bridge.UPSTREAM.equals(record.get("upstream_commit"))
                ||!scenario.equals(record.get("scenario"))||!Boolean.FALSE.equals(record.get("source_authority_created"))||!Boolean.TRUE.equals(record.get("simulation_only"))||!Boolean.FALSE.equals(record.get("hardware_qualified")))throw new IOException("Unsupported original prepared fixture manifest");
        Path directory=config.getConfigurationDirectory().toPath().toAbsolutePath().normalize();
        if(!directory.toString().equals(record.get("config_directory"))||manifest.startsWith(directory)||Files.isSymbolicLink(directory)||!directory.toRealPath().equals(directory))throw new IOException("Restart uses a different original fixture directory");
        // Validate the old inventory's closed schema, without claiming that it describes current bytes.
        if(!(record.get("files") instanceof List)||((List<?>)record.get("files")).isEmpty()||((List<?>)record.get("files")).size()>MAX_FILES)throw new IOException("Invalid original fixture inventory");
        Set<String> names=new HashSet<>();long total=0;
        for(Object raw:(List<?>)record.get("files")){
            if(!(raw instanceof Map))throw new IOException("Invalid original inventory row");Map<?,?> row=(Map<?,?>)raw;
            if(!row.keySet().equals(Set.of("path","sha256","bytes"))||!(row.get("path") instanceof String)||!(row.get("sha256") instanceof String)||!((String)row.get("sha256")).matches("[a-f0-9]{64}"))throw new IOException("Invalid original inventory fields");
            String name=(String)row.get("path");Path relative=Path.of(name);if(relative.isAbsolute()||!relative.normalize().equals(relative)||name.isEmpty()||name.equals(".")||name.equals("..")||name.contains("\\")||name.startsWith("../")||!names.add(name))throw new IOException("Invalid original fixture path");
            total+=NativeJournalJson.integer(row.get("bytes"),0,MAX_FILE_BYTES);if(total>MAX_TOTAL_BYTES)throw new IOException("Original fixture inventory capacity exceeded");
        }
        RestartAttestation captured=new RestartAttestation(config,manifest,expectedHash,scenario,inventory(directory));captured.check();return captured;
    }

    static Map<String,Object> claimRestart(RestartAttestation attestation, NativeFaultedJobReplacement.RestartPermit permit,
            NativeVacuumSources.InterventionSink sink) throws Exception {
        if(attestation==null||permit==null||sink==null)throw new IOException("Owned restart attestation, permit and durable sink required");
        return NativeVacuumSources.installRestartFixture(attestation,permit,sink);
    }

    /** Process identity and native object references never deserialize from this descriptor. */
    static final class RestartAttestation implements AutoCloseable {
        private final Configuration config;private final Path manifest;private final String expectedHash,scenario;
        private final List<Map<String,Object>> files;private final NativeVacuumSources.RestartGraph graph;
        private final Map<String,Object> descriptor;private boolean used;private volatile boolean closed;
        private RestartAttestation(Configuration config,Path manifest,String expectedHash,String scenario,List<Map<String,Object>> files)throws Exception {
            this.config=config;this.manifest=manifest;this.expectedHash=expectedHash;this.scenario=scenario;this.files=files;
            graph=NativeVacuumSources.captureRestartGraph(config);
            descriptor=NativeSensingReconciliation.freeze(Bridge.map("schema_version",1,"profile","native-gui-restart-fixture-attestation-v1","attestation_id",UUID.randomUUID().toString(),"process_id",ProcessHandle.current().pid(),
                "upstream_commit",Bridge.UPSTREAM,"config_directory",config.getConfigurationDirectory().toPath().toAbsolutePath().normalize().toString(),"prepared_manifest",manifest.toString(),"prepared_manifest_sha256",expectedHash,
                "scenario",scenario,"current_files",files,"current_files_sha256",NativeFaultedJobReplacement.digest(Bridge.map("files",files)),"native_graph",graph.descriptor(),"native_graph_sha256",NativeFaultedJobReplacement.digest(graph.descriptor()),
                "original_inventory_is_current",false,"source_authority_created",false,"physical_occupancy_verified",false,"execution_authority_restored",false,"simulation_only",true,"hardware_qualified",false));
        }
        Map<String,Object> descriptor(){return descriptor;}
        Configuration configuration(){return config;}String scenario(){return scenario;}
        void check()throws Exception {
            if(closed)throw new IOException("Restart fixture attestation is closed");requireRestartConfiguration(config,used);
            if(!Files.isRegularFile(manifest,LinkOption.NOFOLLOW_LINKS)||Files.isSymbolicLink(manifest)||!manifest.toRealPath().equals(manifest)||Files.size(manifest)>1024*1024||!expectedHash.equals(hash(Files.readAllBytes(manifest))))throw new IOException("Original prepared manifest changed after restart capture");
            Path directory=config.getConfigurationDirectory().toPath().toAbsolutePath().normalize();if(!directory.toRealPath().equals(directory))throw new IOException("Restart fixture path changed after capture");
            validateInventory(inventory(directory),files);graph.check();
        }
        synchronized void consume()throws Exception {
            if(used||closed||!config.getMachine().isTask(Thread.currentThread()))throw new IOException("Restart fixture attestation is consumed or outside native executor");check();used=true;
        }
        public void close(){closed=true;}
    }
    private static void requireRestartConfiguration(Configuration config,boolean sourceMayExist)throws Exception {
        if(config==null||config!=Configuration.get()||config.getMachine()==null||config.getMachine().isEnabled()||config.getMachine().isHomed()
                ||config.getMachine().isBusy()&&!config.getMachine().isTask(Thread.currentThread()))throw new IOException("Current disabled, unhomed, quiescent restart configuration required");
        Bridge.verifyNativeSimulatorClasses(config.getMachine());NativeVacuumSources.requireBoundedMachine(config);NativeVacuumSources.requireLifecycleSafe(config);
        if(config.getMachine().getDrivers().size()!=1||config.getMachine().getDrivers().get(0).getClass()!=NullDriver.class)throw new IOException("One exact restart simulator driver required");
        if(!sourceMayExist&&((NullDriver)config.getMachine().getDrivers().get(0)).getControlledVacuumSource()!=null)throw new IOException("Restart attestation cannot replace an existing source");
    }

    static final class Prepared {
        private final Configuration config; private final String scenario; private boolean used;
        private Prepared(Configuration config,String scenario){this.config=config;this.scenario=scenario;}
        synchronized void consume(Configuration current,String expectedScenario)throws IOException {
            if(used || current!=config || Configuration.get()!=config || !scenario.equals(expectedScenario)) throw new IOException("Prepared GUI fixture claim is stale or consumed");
            used=true;
        }
    }

    private static void validateInventory(List<Map<String,Object>> current,Object raw)throws Exception {
        if(!(raw instanceof List) || ((List<?>)raw).size()!=current.size()) throw new IOException("Prepared configuration inventory changed");
        List<?> supplied=(List<?>)raw;
        for(int i=0;i<current.size();i++) {
            if(!(supplied.get(i) instanceof Map)) throw new IOException("Malformed prepared file record");
            Map<?,?> row=(Map<?,?>)supplied.get(i);Map<String,Object> live=current.get(i);
            if(!row.keySet().equals(Set.of("path","sha256","bytes")) || !Objects.equals(row.get("path"),live.get("path"))
                    || !Objects.equals(row.get("sha256"),live.get("sha256"))
                    || NativeJournalJson.integer(row.get("bytes"),0,MAX_FILE_BYTES)!=((Number)live.get("bytes")).longValue()) throw new IOException("Prepared configuration file changed");
        }
    }
    private static List<Map<String,Object>> inventory(Path root)throws Exception {
        List<Path> files=new ArrayList<>();
        try(java.util.stream.Stream<Path> stream=Files.walk(root)) {
            Iterator<Path> iter=stream.iterator();int entries=0;
            while(iter.hasNext()) {
                Path p=iter.next(); if(++entries>4096)throw new IOException("Prepared configuration directory is too large");
                Path rel=root.relativize(p);if(rel.getNameCount()>0 && rel.getName(0).toString().equals("scripts"))continue;
                if(Files.isSymbolicLink(p))throw new IOException("Prepared configuration contains symbolic links");
                if(Files.isRegularFile(p,LinkOption.NOFOLLOW_LINKS))files.add(p);
                else if(!Files.isDirectory(p,LinkOption.NOFOLLOW_LINKS))throw new IOException("Prepared configuration contains special files");
                if(files.size()>MAX_FILES)throw new IOException("Too many prepared configuration files");
            }
        }
        files.sort(Comparator.comparing(p->root.relativize(p).toString()));List<Map<String,Object>> rows=new ArrayList<>();long total=0;
        for(Path p:files) {
            long size=Files.size(p);if(size>MAX_FILE_BYTES || (total+=size)>MAX_TOTAL_BYTES)throw new IOException("Prepared configuration file capacity exceeded");
            rows.add(Map.of("path",root.relativize(p).toString().replace('\\','/'),"sha256",hash(Files.readAllBytes(p)),"bytes",size));
        }
        return rows;
    }
    private static String hash(byte[] bytes)throws Exception {StringBuilder encoded=new StringBuilder(64);for(byte value:MessageDigest.getInstance("SHA-256").digest(bytes))encoded.append(String.format(Locale.ROOT,"%02x",value));return encoded.toString();}
}
