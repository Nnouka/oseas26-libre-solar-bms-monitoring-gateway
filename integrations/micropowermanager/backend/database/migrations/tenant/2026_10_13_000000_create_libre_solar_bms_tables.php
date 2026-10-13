<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration {
    public function up(): void {
        Schema::connection('tenant')->create('libre_solar_bms_devices', function (Blueprint $table) {
            $table->id();
            $table->string('device_id')->unique();
            $table->string('name');
            $table->string('state')->nullable();
            $table->decimal('soc_pct', 5, 1)->nullable();
            $table->decimal('soh_pct', 5, 1)->nullable();
            $table->decimal('cycles', 10, 2)->nullable();
            $table->decimal('pack_voltage_v', 7, 2)->nullable();
            $table->decimal('imbalance_v', 6, 3)->nullable();
            $table->decimal('max_temp_c', 5, 1)->nullable();
            $table->string('health')->nullable();
            $table->unsignedTinyInteger('health_score')->nullable();
            $table->json('health_reasons')->nullable();
            $table->json('active_faults')->nullable();
            $table->timestamp('summary_at')->nullable();
            $table->timestamp('last_seen_at')->nullable();
            $table->timestamps();
        });

        Schema::connection('tenant')->create('libre_solar_bms_events', function (Blueprint $table) {
            $table->id();
            $table->unsignedBigInteger('libre_solar_bms_device_id')->index();
            $table->string('event_key', 40)->unique();
            $table->timestamp('occurred_at');
            $table->string('type');
            $table->string('fault')->nullable();
            $table->string('severity');
            $table->string('message');
            $table->text('action')->nullable();
            $table->timestamps();
        });
    }

    public function down(): void {
        Schema::connection('tenant')->dropIfExists('libre_solar_bms_events');
        Schema::connection('tenant')->dropIfExists('libre_solar_bms_devices');
    }
};
