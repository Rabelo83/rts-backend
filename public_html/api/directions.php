<?php
require_once "config.php";

$route_id = $_GET["route_id"] ?? "";

$data = call_bustime("getdirections", [
    "rt" => $route_id
]);

$dirs_raw = $data["directions"] ?? [];

$dirs_clean = [];
foreach ($dirs_raw as $d) {
    $dirs_clean[] = [
        "id" => $d["id"] ?? null,
        "name" => $d["name"] ?? null
    ];
}

echo json_encode(["directions" => $dirs_clean]);