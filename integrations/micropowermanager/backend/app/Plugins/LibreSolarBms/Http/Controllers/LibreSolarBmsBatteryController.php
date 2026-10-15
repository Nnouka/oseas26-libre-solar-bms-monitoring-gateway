<?php

namespace App\Plugins\LibreSolarBms\Http\Controllers;

use App\Http\Controllers\Controller;
use App\Http\Resources\ApiResource;
use App\Plugins\LibreSolarBms\Http\Requests\HistoryQueryRequest;
use App\Plugins\LibreSolarBms\Services\LibreSolarBmsService;
use Dedoc\Scramble\Attributes\Group;
use Illuminate\Support\Carbon;

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

    public function summaries(HistoryQueryRequest $request, int $batteryId): ApiResource {
        return ApiResource::make(
            $this->service->summaries($batteryId, $request->validated(), $request->integer('per_page', 25))
        );
    }

    public function trend(HistoryQueryRequest $request, int $batteryId): ApiResource {
        $to = $request->filled('to') ? Carbon::parse($request->input('to')) : Carbon::now();
        $from = $request->filled('from') ? Carbon::parse($request->input('from')) : $to->copy()->subDay();

        return ApiResource::make($this->service->trend($batteryId, $from, $to, $request->integer('buckets', 96)));
    }

    public function events(HistoryQueryRequest $request, int $batteryId): ApiResource {
        $limit = min($request->integer('limit', self::EVENT_LIMIT_DEFAULT), self::EVENT_LIMIT_MAX);
        $perPage = max($request->integer('per_page', $limit), 1);

        return ApiResource::make($this->service->events($batteryId, $request->validated(), $perPage));
    }
}
