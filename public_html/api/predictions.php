<?php
require_once "config.php";

$stop_id = $_GET["stop_id"] ?? "";

$data = call_bustime("getpredictions", [
    "stpid" => $stop_id
]);

$preds_raw = $data["prd"] ?? [];

$preds_clean = [];
foreach ($preds_raw as $p) {
    $preds_clean[] = [
        "route"        => $p["rt"] ?? null,
        "direction"    => $p["rtdir"] ?? null,
        "destination"  => $p["des"] ?? null,
        "minutes"      => $p["prdctdn"] ?? null,  // "4", "12", or "DUE"
        "vehicle_id"   => $p["vid"] ?? null,
        "arrival_time" => $p["prdtm"] ?? null,
        "delayed"      => $p["dly"] ?? null
    ];
}

echo json_encode(["predictions" => $preds_clean]);