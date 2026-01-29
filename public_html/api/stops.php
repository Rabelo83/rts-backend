<?php
require_once "config.php";

$route_id = $_GET["route_id"] ?? "";
$direction_id = $_GET["direction_id"] ?? "";

$data = call_bustime("getstops", [
    "rt" => $route_id,
    "dir" => $direction_id
]);

$stops_raw = $data["stops"] ?? [];

$stops_clean = [];
foreach ($stops_raw as $s) {
    $stops_clean[] = [
        "id" => $s["stpid"] ?? null,
        "name" => $s["stpnm"] ?? null,
        "lat" => $s["lat"] ?? null,
        "lon" => $s["lon"] ?? null
    ];
}

echo json_encode(["stops" => $stops_clean]);