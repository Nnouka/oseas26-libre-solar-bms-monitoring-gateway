<?php

namespace App\Plugins\LibreSolarBms\Http\Controllers;

use App\Http\Controllers\Controller;
use App\Http\Resources\ApiResource;
use App\Plugins\LibreSolarBms\Services\LibreSolarBmsService;
use Dedoc\Scramble\Attributes\Group;
use Illuminate\Http\Request;

#[Group('Plugins / Libre Solar BMS', 'Battery health for the MPM web client')]
class LibreSolarBmsBatteryController extends Controller {
    private const EVENT_LIMIT_DEFAULT = 50;
    private const EVENT_LIMIT_MAX = 200;

    public function __construct(
        private LibreSolarBmsService $service,
    ) {}

    public function index(): ApiResource {
        return ApiResource::make($this->service->listBatteries());
    }

    public function show(int $batteryId): ApiResource {
        return ApiResource::make($this->service->getBattery($batteryId));
    }

    public function events(Request $request, int $batteryId): ApiResource {
        $limit = min($request->integer('limit', self::EVENT_LIMIT_DEFAULT), self::EVENT_LIMIT_MAX);

        return ApiResource::make($this->service->latestEvents($batteryId, max($limit, 1)));
    }
}
