<?php
require_once "config.php";

$data = call_bustime("getroutes", []);
$routes_raw = $data["routes"] ?? [];

$routes_clean = [];
foreach ($routes_raw as $r) {
    $routes_clean[] = [
        "id" => $r["rt"] ?? null,
        "name" => $r["rtnm"] ?? null,
        "color" => $r["rtclr"] ?? null
    ];
}

echo json_encode(["routes" => $routes_clean]);