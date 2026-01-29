<?php
require_once "config.php";

$route_id = $_GET["route_id"] ?? "";

$data = call_bustime("getvehicles", [
    "rt" => $route_id
]);

$veh_raw = $data["vehicle"] ?? [];

$veh_clean = [];
foreach ($veh_raw as $v) {
    $veh_clean[] = [
        "vehicle_id" => $v["vid"] ?? null,
        "lat"        => $v["lat"] ?? null,
        "lon"        => $v["lon"] ?? null,
        "heading"    => $v["hdg"] ?? null,
        "speed"      => $v["spd"] ?? null,
        "route"      => $v["rt"] ?? null,
        "destination"=> $v["des"] ?? null,
        "delayed"    => $v["dly"] ?? null
    ];
}

echo json_encode(["vehicles" => $veh_clean]);