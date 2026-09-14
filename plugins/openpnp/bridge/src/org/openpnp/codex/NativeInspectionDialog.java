/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.openpnp.codex;

import java.awt.BorderLayout;
import java.awt.Dialog;
import java.awt.Dimension;
import java.awt.GridLayout;
import java.awt.Window;
import java.awt.event.WindowAdapter;
import java.awt.event.WindowEvent;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Consumer;
import javax.swing.BorderFactory;
import javax.swing.DefaultCellEditor;
import javax.swing.JButton;
import javax.swing.JComboBox;
import javax.swing.JDialog;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JSplitPane;
import javax.swing.JTable;
import javax.swing.JTextArea;
import javax.swing.JTextField;
import javax.swing.SwingUtilities;
import javax.swing.WindowConstants;
import javax.swing.table.AbstractTableModel;
import javax.swing.table.DefaultTableCellRenderer;

/** Local, operator-reported observations. The form does not authenticate a person or instrument. */
final class NativeInspectionDialog extends JDialog {
    private final Map<String,Object> task;
    private final Consumer<Map<String,Object>> submitAction;
    private final Runnable cancelAction;
    private final JTextField operator = new JTextField(), xyTolerance = new JTextField(), rotationTolerance = new JTextField();
    private final JTextArea note = new JTextArea(3, 50);
    private final ObservationTable observations;
    private final ArtifactTable artifacts = new ArtifactTable();
    private final JTable observationTable, artifactTable;
    private final JButton submit = new JButton("Save inspection"), cancel = new JButton("Close inspection");
    private final JButton addArtifact = new JButton("Add evidence reference"), removeArtifact = new JButton("Remove reference");
    private final JTextArea message = new JTextArea("Enter your observations. Native completion does not verify assembly quality.");
    private boolean pending;

    NativeInspectionDialog(Window owner, Map<String,Object> immutableTask,
            Consumer<Map<String,Object>> submitAction, Runnable cancelAction) {
        // A child modeless window stays usable under the ownership dialog's application modality.
        super(owner, "Inspect completed board", Dialog.ModalityType.MODELESS);
        requireEdt();
        this.task = immutableTask;
        this.submitAction = submitAction;
        this.cancelAction = cancelAction;
        observations = new ObservationTable(placements(immutableTask));
        observationTable = new JTable(observations);
        artifactTable = new JTable(artifacts);
        setDefaultCloseOperation(WindowConstants.DO_NOTHING_ON_CLOSE);
        addWindowListener(new WindowAdapter() {
            @Override public void windowClosing(WindowEvent event) { cancelAction.run(); }
        });

        JPanel content = new JPanel(new BorderLayout(8, 8));
        content.setBorder(BorderFactory.createEmptyBorder(12, 12, 12, 12));
        JTextArea context = readonly(contextText(immutableTask));
        context.setRows(6);
        JScrollPane contextScroll = new JScrollPane(context);
        contextScroll.setBorder(BorderFactory.createTitledBorder("Board and completion context — fixed for this inspection"));
        content.add(contextScroll, BorderLayout.NORTH);

        observationTable.setAutoResizeMode(JTable.AUTO_RESIZE_OFF);
        observationTable.setRowHeight(24);
        observationTable.setDefaultRenderer(Object.class, plainRenderer());
        String[] presence = {"unknown", "present", "missing"};
        String[] polarity = {"unknown", "correct", "incorrect", "not applicable"};
        observationTable.getColumnModel().getColumn(3).setCellEditor(new DefaultCellEditor(new JComboBox<>(presence)));
        observationTable.getColumnModel().getColumn(4).setCellEditor(new DefaultCellEditor(new JComboBox<>(polarity)));
        for (int i = 0; i < observations.getColumnCount(); i++)
            observationTable.getColumnModel().getColumn(i).setPreferredWidth(i == 2 ? 235 : i < 2 ? 145 : 125);
        JScrollPane rows = new JScrollPane(observationTable);
        rows.setBorder(BorderFactory.createTitledBorder("Observations — offsets in the board's coordinate frame (mm and degrees)"));

        JPanel details = new JPanel(new BorderLayout(8, 8));
        JPanel fields = new JPanel(new GridLayout(2, 3, 8, 4));
        fields.add(plainLabel("Operator label")); fields.add(plainLabel("XY tolerance (mm, > 0 to 100)")); fields.add(plainLabel("Rotation tolerance (degrees, > 0 to 180)"));
        operator.setName("inspection.operator_label"); xyTolerance.setName("inspection.xy_tolerance"); rotationTolerance.setName("inspection.rotation_tolerance");
        fields.add(operator); fields.add(xyTolerance); fields.add(rotationTolerance);
        details.add(fields, BorderLayout.NORTH);
        JPanel evidence = new JPanel(new GridLayout(2, 1, 4, 4));
        note.setName("inspection.operator_note"); note.setLineWrap(true); note.setWrapStyleWord(true);
        JScrollPane noteScroll = new JScrollPane(note);
        noteScroll.setBorder(BorderFactory.createTitledBorder("Required note — describe how you inspected the board and any limitations"));
        evidence.add(noteScroll);
        artifactTable.setDefaultRenderer(Object.class, plainRenderer());
        artifactTable.getColumnModel().getColumn(2).setCellEditor(new DefaultCellEditor(new JComboBox<>(new String[]{"image", "measurement-file", "operator-note"})));
        JPanel references = new JPanel(new BorderLayout());
        references.add(new JScrollPane(artifactTable), BorderLayout.CENTER);
        JPanel referenceButtons = new JPanel(); referenceButtons.add(addArtifact); referenceButtons.add(removeArtifact);
        references.add(referenceButtons, BorderLayout.SOUTH);
        references.setBorder(BorderFactory.createTitledBorder("Optional evidence references — existing owned artifacts only"));
        evidence.add(references); details.add(evidence, BorderLayout.CENTER);
        JSplitPane split = new JSplitPane(JSplitPane.VERTICAL_SPLIT, rows, details);
        split.setResizeWeight(0.60); content.add(split, BorderLayout.CENTER);

        JPanel bottom = new JPanel(new BorderLayout(4, 4));
        message.setEditable(false); message.setLineWrap(true); message.setWrapStyleWord(true); message.setOpaque(false); message.setRows(2);
        message.setName("inspection.message"); bottom.add(message, BorderLayout.CENTER);
        JPanel buttons = new JPanel(); buttons.add(submit); buttons.add(cancel); bottom.add(buttons, BorderLayout.EAST);
        content.add(bottom, BorderLayout.SOUTH);
        observationTable.setName("inspection.observations"); artifactTable.setName("inspection.artifacts"); submit.setName("inspection.submit"); cancel.setName("inspection.cancel");
        addArtifact.addActionListener(e -> { if (!pending && artifacts.rows.size() < 32) { artifacts.rows.add(new String[]{"", "", "image"}); artifacts.fireTableDataChanged(); } });
        removeArtifact.addActionListener(e -> { int index = artifactTable.getSelectedRow(); if (!pending && index >= 0) { artifacts.rows.remove(index); artifacts.fireTableDataChanged(); } });
        submit.addActionListener(e -> submit()); cancel.addActionListener(e -> cancelAction.run());
        setContentPane(content); setMinimumSize(new Dimension(850, 650)); setSize(1220, 850); setLocationRelativeTo(owner);
    }

    /** Call only on EDT after asynchronous completion; never waits on native or durable work. */
    void submissionFinished(Throwable failure) {
        requireEdt();
        if (failure == null) { dispose(); return; }
        pending = false; setInputsEnabled(true);
        Throwable cause = failure;
        while ((cause instanceof java.util.concurrent.CompletionException || cause instanceof java.util.concurrent.ExecutionException) && cause.getCause() != null) cause = cause.getCause();
        String detail = cause.getMessage();
        if (detail == null || detail.isBlank()) detail = cause.getClass().getSimpleName();
        message.setText("Inspection was not saved: " + detail.substring(0, Math.min(1000, detail.length())));
    }

    private void submit() {
        if (pending) return;
        try {
            if (observationTable.isEditing() && !observationTable.getCellEditor().stopCellEditing()) throw new IllegalArgumentException("Finish the current observation first.");
            if (artifactTable.isEditing() && !artifactTable.getCellEditor().stopCellEditing()) throw new IllegalArgumentException("Finish the evidence reference first.");
            Map<String,Object> result = new LinkedHashMap<>();
            result.put("operator_label", text(operator.getText(), 128, "Operator label"));
            result.put("operator_note", text(note.getText(), 4000, "Inspection note"));
            BigDecimal xy = number(xyTolerance.getText(), "XY tolerance"), rotation = number(rotationTolerance.getText(), "Rotation tolerance");
            if (xy.signum() <= 0 || xy.compareTo(BigDecimal.valueOf(100)) > 0 || rotation.signum() <= 0 || rotation.compareTo(BigDecimal.valueOf(180)) > 0) throw new IllegalArgumentException("Enter positive tolerances within the displayed ranges.");
            Map<String,Object> tolerances = new LinkedHashMap<>(); tolerances.put("xy_mm", xy); tolerances.put("rotation_deg", rotation);
            result.put("tolerances", tolerances); result.put("records", observations.values()); result.put("artifact_refs", artifacts.values());
            Map<String,Object> frozen = immutableMap(result);
            pending = true; setInputsEnabled(false);
            message.setText("Saving the observations against this board's current completion context…");
            submitAction.accept(frozen);
        } catch (RuntimeException failure) { submissionFinished(failure); }
    }

    private void setInputsEnabled(boolean enabled) {
        operator.setEnabled(enabled); xyTolerance.setEnabled(enabled); rotationTolerance.setEnabled(enabled); note.setEnabled(enabled);
        observationTable.setEnabled(enabled); artifactTable.setEnabled(enabled); addArtifact.setEnabled(enabled); removeArtifact.setEnabled(enabled); submit.setEnabled(enabled);
        // Closing and the parent ownership controls remain available during asynchronous submission.
    }

    private static final class ObservationTable extends AbstractTableModel {
        private static final String[] COLUMNS = {"Placement", "Part", "Native target: X / Y / Z / rotation", "Presence", "Polarity", "X offset (mm)", "Y offset (mm)", "Rotation error (deg)", "XY uncertainty (mm)", "Rotation uncertainty (deg)"};
        private static final String[] NUMERIC = {"dx_mm", "dy_mm", "rotation_deg", "xy_uncertainty_mm", "rotation_uncertainty_deg"};
        private final List<Map<String,Object>> required;
        private final String[][] input;
        ObservationTable(List<Map<String,Object>> required) {
            this.required = required; input = new String[required.size()][7];
            for (String[] row : input) { java.util.Arrays.fill(row, ""); row[0] = "unknown"; row[1] = "unknown"; }
        }
        @Override public int getRowCount() { return required.size(); }
        @Override public int getColumnCount() { return COLUMNS.length; }
        @Override public String getColumnName(int column) { return COLUMNS[column]; }
        @Override public Object getValueAt(int row, int column) {
            Map<String,Object> placement = required.get(row);
            if (column == 0) return placement.get("placement_id");
            if (column == 1) return placement.get("part_id");
            if (column == 2) {
                Map<?,?> location = (Map<?,?>) placement.get("location");
                return location.get("x") + " / " + location.get("y") + " / " + location.get("z") + " / " + location.get("rotation");
            }
            return input[row][column - 3];
        }
        @Override public boolean isCellEditable(int row, int column) { return column == 3 || (column >= 4 && "present".equals(input[row][0])); }
        @Override public void setValueAt(Object value, int row, int column) {
            if (!isCellEditable(row, column)) return;
            input[row][column - 3] = value == null ? "" : value.toString();
            if (column == 3 && !"present".equals(input[row][0])) { input[row][1] = "unknown"; for (int i = 2; i < 7; i++) input[row][i] = ""; }
            fireTableRowsUpdated(row, row);
        }
        List<Map<String,Object>> values() {
            List<Map<String,Object>> result = new ArrayList<>();
            for (int i = 0; i < required.size(); i++) {
                Map<String,Object> row = new LinkedHashMap<>(); row.put("holder_instance_id", required.get(i).get("holder_instance_id")); row.put("placement_id", required.get(i).get("placement_id"));
                String presence = input[i][0], polarity = input[i][1].replace(' ', '_');
                if (!List.of("present", "missing", "unknown").contains(presence) || !List.of("correct", "incorrect", "unknown", "not_applicable").contains(polarity)) throw new IllegalArgumentException("Choose presence and polarity for every placement.");
                row.put("presence", presence); row.put("polarity", polarity);
                if ("present".equals(presence)) for (int j = 0; j < NUMERIC.length; j++) {
                    BigDecimal value = number(input[i][j + 2], "Placement " + required.get(i).get("placement_id") + ": " + COLUMNS[j + 5]);
                    if (j >= 3 && value.signum() < 0) throw new IllegalArgumentException("Uncertainty must be zero or positive.");
                    int maximum = j == 2 ? 360 : j == 4 ? 180 : 1000000;
                    if (value.abs().compareTo(BigDecimal.valueOf(maximum)) > 0) throw new IllegalArgumentException(COLUMNS[j + 5] + " must be within " + maximum + ".");
                    row.put(NUMERIC[j], value);
                }
                result.add(row);
            }
            return result;
        }
    }

    private static final class ArtifactTable extends AbstractTableModel {
        private final List<String[]> rows = new ArrayList<>();
        @Override public int getRowCount() { return rows.size(); }
        @Override public int getColumnCount() { return 3; }
        @Override public String getColumnName(int column) { return new String[]{"Artifact ID", "SHA-256", "Kind"}[column]; }
        @Override public Object getValueAt(int row, int column) { return rows.get(row)[column]; }
        @Override public boolean isCellEditable(int row, int column) { return true; }
        @Override public void setValueAt(Object value, int row, int column) { rows.get(row)[column] = value == null ? "" : value.toString(); fireTableCellUpdated(row, column); }
        List<Map<String,Object>> values() {
            List<Map<String,Object>> result = new ArrayList<>();
            if (rows.size() > 32) throw new IllegalArgumentException("At most 32 evidence references are allowed.");
            for (String[] row : rows) {
                String id = row[0].trim(), hash = row[1].trim(), kind = row[2];
                try { if (!UUID.fromString(id).toString().equals(id)) throw new IllegalArgumentException(); }
                catch (IllegalArgumentException failure) { throw new IllegalArgumentException("Each evidence reference needs its canonical artifact ID."); }
                if (!hash.matches("[0-9a-f]{64}") || !List.of("image", "measurement-file", "operator-note").contains(kind)) throw new IllegalArgumentException("Each reference needs a lowercase SHA-256 and a listed evidence kind.");
                Map<String,Object> reference = new LinkedHashMap<>(); reference.put("artifact_id", id); reference.put("sha256", hash); reference.put("kind", kind); result.add(reference);
            }
            return result;
        }
    }

    @SuppressWarnings("unchecked") static Map<String,Object> immutableMap(Map<String,Object> value) { return (Map<String,Object>) freeze(value, 0); }
    private static Object freeze(Object value, int depth) {
        if (depth > 20) throw new IllegalArgumentException("Inspection context is too deep.");
        if (value == null || value instanceof String || value instanceof Boolean) return value;
        if (value instanceof Number) {
            if (!(value instanceof Byte || value instanceof Short || value instanceof Integer || value instanceof Long || value instanceof Float || value instanceof Double || value instanceof java.math.BigInteger || value instanceof java.math.BigDecimal)) throw new IllegalArgumentException("Inspection context contains a mutable or unsupported number.");
            if (!Double.isFinite(((Number)value).doubleValue())) throw new IllegalArgumentException("Inspection context numbers must be finite.");
            return value;
        }
        if (value instanceof Map) {
            Map<String,Object> result = new LinkedHashMap<>();
            for (Map.Entry<?,?> entry : ((Map<?,?>) value).entrySet()) {
                if (!(entry.getKey() instanceof String)) throw new IllegalArgumentException("Inspection context keys must be text.");
                result.put((String) entry.getKey(), freeze(entry.getValue(), depth + 1));
            }
            return Collections.unmodifiableMap(result);
        }
        if (value instanceof List) { List<Object> result = new ArrayList<>(); for (Object item : (List<?>) value) result.add(freeze(item, depth + 1)); return Collections.unmodifiableList(result); }
        throw new IllegalArgumentException("Inspection context contains an unsupported value.");
    }
    @SuppressWarnings("unchecked") static List<Map<String,Object>> placements(Map<String,Object> task) {
        Map<String,Object> snapshot = snapshot(task);
        Object rows = snapshot.get("required_placements");
        if (!(rows instanceof List) || ((List<?>) rows).isEmpty() || ((List<?>) rows).size() > 200) throw new IllegalArgumentException("Inspection needs one to 200 required placements.");
        for (Object row : (List<?>) rows) {
            if (!(row instanceof Map) || !(((Map<?,?>) row).get("location") instanceof Map)) throw new IllegalArgumentException("Inspection placement context is incomplete.");
            for (String key : List.of("holder_instance_id", "placement_id", "part_id")) if (!(((Map<?,?>) row).get(key) instanceof String)) throw new IllegalArgumentException("Inspection placement identity is incomplete.");
            Map<?,?> location = (Map<?,?>)((Map<?,?>)row).get("location");
            if (!"mm".equals(location.get("units"))) throw new IllegalArgumentException("Inspection target coordinates must be in millimeters.");
            for (String key : List.of("x", "y", "z", "rotation")) if (!(location.get(key) instanceof Number) || !Double.isFinite(((Number)location.get(key)).doubleValue())) throw new IllegalArgumentException("Inspection target coordinates must be finite.");
        }
        return (List<Map<String,Object>>) rows;
    }
    private static String contextText(Map<String,Object> task) {
        Map<String,Object> snapshot = snapshot(task);
        StringBuilder value = new StringBuilder("Simulator only. These observations are locally reported; a person or instrument is not authenticated.\n");
        value.append("Board side: ").append(snapshot.get("board_side")).append("    Required placements: ").append(snapshot.get("required_count")).append('\n');
        // Keep every exact identity and lineage field visible, including future context additions.
        for (Map.Entry<String,Object> entry : task.entrySet()) if (!"required_placements".equals(entry.getKey()) && !"snapshot".equals(entry.getKey())) value.append(entry.getKey()).append(": ").append(entry.getValue()).append('\n');
        if (snapshot != task) for (Map.Entry<String,Object> entry : snapshot.entrySet()) if (!"required_placements".equals(entry.getKey())) value.append(entry.getKey()).append(": ").append(entry.getValue()).append('\n');
        return value.toString();
    }
    @SuppressWarnings("unchecked") private static Map<String,Object> snapshot(Map<String,Object> task) { return task.get("snapshot") instanceof Map ? (Map<String,Object>) task.get("snapshot") : task; }
    private static JTextArea readonly(String text) { JTextArea area = new JTextArea(text); area.setEditable(false); area.setLineWrap(true); area.setWrapStyleWord(true); area.setCaretPosition(0); return area; }
    private static JLabel plainLabel(String text) { JLabel label = new JLabel(text); label.putClientProperty("html.disable", Boolean.TRUE); return label; }
    private static DefaultTableCellRenderer plainRenderer() { DefaultTableCellRenderer renderer = new DefaultTableCellRenderer(); renderer.putClientProperty("html.disable", Boolean.TRUE); return renderer; }
    private static String text(String value, int limit, String label) { String result = value.trim(); if (result.isEmpty() || result.length() > limit) throw new IllegalArgumentException(label + " must contain 1 to " + limit + " characters."); return result; }
    private static BigDecimal number(String value, String label) {
        try {
            String input = value.trim();
            if (input.length() > 128) throw new NumberFormatException();
            BigDecimal number = new BigDecimal(input);
            if (Math.abs((long)number.scale()) > 1000 || !Double.isFinite(number.doubleValue())) throw new NumberFormatException();
            return number; // Preserve the entered decimal through the model's exact tolerance comparison.
        } catch (NumberFormatException failure) { throw new IllegalArgumentException(label + " needs a finite decimal number (at most 128 characters)."); }
    }
    private static void requireEdt() { if (!SwingUtilities.isEventDispatchThread()) throw new IllegalStateException("Inspection form requires EDT"); }
}
