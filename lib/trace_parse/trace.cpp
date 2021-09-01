/*-
 *   BSD LICENSE
 *
 *   Copyright (c) Intel Corporation.
 *   All rights reserved.
 *
 *   Redistribution and use in source and binary forms, with or without
 *   modification, are permitted provided that the following conditions
 *   are met:
 *
 *     * Redistributions of source code must retain the above copyright
 *       notice, this list of conditions and the following disclaimer.
 *     * Redistributions in binary form must reproduce the above copyright
 *       notice, this list of conditions and the following disclaimer in
 *       the documentation and/or other materials provided with the
 *       distribution.
 *     * Neither the name of Intel Corporation nor the names of its
 *       contributors may be used to endorse or promote products derived
 *       from this software without specific prior written permission.
 *
 *   THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 *   "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 *   LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 *   A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 *   OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 *   SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 *   LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 *   DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 *   THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 *   (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 *   OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#include "spdk/stdinc.h"
#include "spdk/likely.h"
#include "spdk/log.h"
#include "spdk/trace.h"
#include "spdk/util.h"

#include <map>

struct entry_key {
	entry_key(uint16_t _lcore, uint64_t _tsc) : lcore(_lcore), tsc(_tsc) {}
	uint16_t lcore;
	uint64_t tsc;
};

class compare_entry_key
{
public:
	bool operator()(const entry_key &first, const entry_key &second) const
	{
		if (first.tsc == second.tsc) {
			return first.lcore < second.lcore;
		} else {
			return first.tsc < second.tsc;
		}
	}
};

typedef std::map<entry_key, spdk_trace_entry *, compare_entry_key> entry_map;

struct argument_context {
	struct spdk_trace_parser	*parser;
	struct spdk_trace_entry		*entry;
	struct spdk_trace_entry_buffer	*buffer;
	uint16_t			lcore;
	size_t				offset;

	argument_context(struct spdk_trace_parser *parser, struct spdk_trace_entry *entry,
			 uint16_t lcore) : parser(parser), entry(entry), lcore(lcore)
	{
		buffer = (struct spdk_trace_entry_buffer *)entry;

		/* The first argument resides within the spdk_trace_entry structure, so the initial
		 * offset needs to be adjusted to the start of the spdk_trace_entry.args array
		 */
		offset = offsetof(struct spdk_trace_entry, args) -
			 offsetof(struct spdk_trace_entry_buffer, data);
	}
};

struct object_stats {
	std::map<uint64_t, uint64_t>	index;
	std::map<uint64_t, uint64_t>	start;
	uint64_t			counter;
};

struct spdk_trace_parser {
	struct spdk_trace_histories		*histories;
	size_t					map_size;
	int					fd;
	uint64_t				tsc_offset;
	entry_map				entries;
	entry_map::iterator			iter;
	object_stats				stats[SPDK_TRACE_MAX_OBJECT];
};

static struct spdk_trace_entry_buffer *
get_next_buffer(struct spdk_trace_parser *parser, struct spdk_trace_entry_buffer *buf,
		uint16_t lcore)
{
	struct spdk_trace_history *history;

	history = spdk_get_per_lcore_history(parser->histories, lcore);
	assert(history);

	if (spdk_unlikely((void *)buf == &history->entries[history->num_entries - 1])) {
		return (struct spdk_trace_entry_buffer *)&history->entries[0];
	} else {
		return buf + 1;
	}
}

static bool
build_arg(struct argument_context *argctx, const struct spdk_trace_argument *arg, int argid,
	  struct spdk_trace_parser_entry *pe)
{
	struct spdk_trace_entry *entry = argctx->entry;
	struct spdk_trace_entry_buffer *buffer = argctx->buffer;
	size_t curlen, argoff;

	argoff = 0;
	while (argoff < arg->size) {
		if (argctx->offset == sizeof(buffer->data)) {
			buffer = get_next_buffer(argctx->parser, buffer, argctx->lcore);
			if (spdk_unlikely(buffer->tpoint_id != SPDK_TRACE_MAX_TPOINT_ID ||
					  buffer->tsc != entry->tsc)) {
				return false;
			}

			argctx->offset = 0;
			argctx->buffer = buffer;
		}

		curlen = spdk_min(sizeof(buffer->data) - argctx->offset, arg->size - argoff);
		if (argoff < sizeof(pe->args[0])) {
			memcpy(&pe->args[argid].string[argoff], &buffer->data[argctx->offset],
			       spdk_min(curlen, sizeof(pe->args[0]) - argoff));
		}

		argctx->offset += curlen;
		argoff += curlen;
	}

	return true;
}

static bool
next_entry(struct spdk_trace_parser *parser, struct spdk_trace_parser_entry *pe)
{
	struct spdk_trace_tpoint *tpoint;
	struct spdk_trace_entry *entry;
	struct object_stats *stats;
	size_t i;

	if (parser->iter == parser->entries.end()) {
		return false;
	}

	entry = parser->iter->second;
	pe->lcore = parser->iter->first.lcore;
	pe->entry = entry;
	tpoint = &parser->histories->flags.tpoint[entry->tpoint_id];
	stats = &parser->stats[tpoint->object_type];

	if (tpoint->new_object) {
		stats->index[entry->object_id] = stats->counter++;
		stats->start[entry->object_id] = entry->tsc;
	}

	if (tpoint->object_type != OBJECT_NONE) {
		if (spdk_likely(stats->start.find(entry->object_id) != stats->start.end())) {
			pe->object_index = stats->index[entry->object_id];
			pe->object_start = stats->start[entry->object_id];
		} else {
			pe->object_index = UINT64_MAX;
			pe->object_start = UINT64_MAX;
		}
	}

	struct argument_context argctx(parser, entry, pe->lcore);
	for (i = 0; i < tpoint->num_args; ++i) {
		if (!build_arg(&argctx, &tpoint->args[i], i, pe)) {
			SPDK_ERRLOG("Failed to parse tracepoint argument\n");
			return false;
		}
	}

	parser->iter++;

	return true;
}

static void
populate_events(struct spdk_trace_parser *parser, struct spdk_trace_history *history,
		int num_entries)
{
	int i, num_entries_filled;
	struct spdk_trace_entry *e;
	int first, last, lcore;

	lcore = history->lcore;
	e = history->entries;

	num_entries_filled = num_entries;
	while (e[num_entries_filled - 1].tsc == 0) {
		num_entries_filled--;
	}

	if (num_entries == num_entries_filled) {
		first = last = 0;
		for (i = 1; i < num_entries; i++) {
			if (e[i].tsc < e[first].tsc) {
				first = i;
			}
			if (e[i].tsc > e[last].tsc) {
				last = i;
			}
		}
	} else {
		first = 0;
		last = num_entries_filled - 1;
	}

	/*
	 * We keep track of the highest first TSC out of all reactors.
	 *  We will ignore any events that occured before this TSC on any
	 *  other reactors.  This will ensure we only print data for the
	 *  subset of time where we have data across all reactors.
	 */
	if (e[first].tsc > parser->tsc_offset) {
		parser->tsc_offset = e[first].tsc;
	}

	i = first;
	while (1) {
		if (e[i].tpoint_id != SPDK_TRACE_MAX_TPOINT_ID) {
			parser->entries[entry_key(lcore, e[i].tsc)] = &e[i];
		}
		if (i == last) {
			break;
		}
		i++;
		if (i == num_entries_filled) {
			i = 0;
		}
	}
}

static struct spdk_trace_parser *
init(const struct spdk_trace_parser_opts *opts)
{
	struct spdk_trace_parser *parser;
	struct spdk_trace_history *history;
	struct stat stat;
	int rc, i;

	parser = new spdk_trace_parser();
	if (parser == NULL) {
		return NULL;
	}

	switch (opts->mode) {
	case SPDK_TRACE_PARSER_MODE_FILE:
		parser->fd = open(opts->filename, O_RDONLY);
		break;
	case SPDK_TRACE_PARSER_MODE_SHM:
		parser->fd = shm_open(opts->filename, O_RDONLY, 0600);
		break;
	default:
		SPDK_ERRLOG("Invalid mode: %d\n", opts->mode);
		parser->fd = -1;
		goto error;
	}

	if (parser->fd < 0) {
		SPDK_ERRLOG("Could not open trace file: %s (%d)\n", opts->filename, errno);
		goto error;
	}

	rc = fstat(parser->fd, &stat);
	if (rc < 0) {
		SPDK_ERRLOG("Could not get size of trace file: %s\n", opts->filename);
		goto error;
	}

	if ((size_t)stat.st_size < sizeof(*parser->histories)) {
		SPDK_ERRLOG("Invalid trace file: %s\n", opts->filename);
		goto error;
	}

	/* Map the header of trace file */
	parser->map_size = sizeof(*parser->histories);
	parser->histories = (struct spdk_trace_histories *)mmap(NULL, parser->map_size, PROT_READ,
			    MAP_SHARED, parser->fd, 0);
	if (parser->histories == MAP_FAILED) {
		SPDK_ERRLOG("Could not mmap trace file: %s\n", opts->filename);
		goto error;
	}

	/* Remap the entire trace file */
	parser->map_size = spdk_get_trace_histories_size(parser->histories);
	munmap(parser->histories, sizeof(*parser->histories));
	if ((size_t)stat.st_size < parser->map_size) {
		SPDK_ERRLOG("Trace file %s is not a valid\n", opts->filename);
		goto error;
	}
	parser->histories = (struct spdk_trace_histories *)mmap(NULL, parser->map_size, PROT_READ,
			    MAP_SHARED, parser->fd, 0);
	if (parser->histories == MAP_FAILED) {
		SPDK_ERRLOG("Could not mmap trace file: %s\n", opts->filename);
		goto error;
	}

	if (opts->lcore == SPDK_TRACE_MAX_LCORE) {
		for (i = 0; i < SPDK_TRACE_MAX_LCORE; i++) {
			history = spdk_get_per_lcore_history(parser->histories, i);
			if (history->num_entries == 0 || history->entries[0].tsc == 0) {
				continue;
			}

			populate_events(parser, history, history->num_entries);
		}
	} else {
		history = spdk_get_per_lcore_history(parser->histories, opts->lcore);
		if (history->num_entries > 0 && history->entries[0].tsc != 0) {
			populate_events(parser, history, history->num_entries);
		}
	}

	parser->iter = parser->entries.begin();

	return parser;
error:
	spdk_trace_parser_cleanup(parser);
	return NULL;
}

static void
cleanup(struct spdk_trace_parser *parser)
{
	if (parser == NULL) {
		return;
	}

	if (parser->histories != NULL) {
		munmap(parser->histories, parser->map_size);
	}

	if (parser->fd > 0) {
		close(parser->fd);
	}

	delete parser;
}

extern "C" {

	struct spdk_trace_parser *
	spdk_trace_parser_init(const struct spdk_trace_parser_opts *opts)
	{
		return init(opts);
	}

	void
	spdk_trace_parser_cleanup(struct spdk_trace_parser *parser)
	{
		cleanup(parser);
	}

	void
	spdk_trace_parser_get_flags(struct spdk_trace_parser *parser,
				    struct spdk_trace_flags **flags)
	{
		*flags = &parser->histories->flags;
	}

	uint64_t
	spdk_trace_parser_get_tsc_offset(struct spdk_trace_parser *parser)
	{
		return parser->tsc_offset;
	}

	bool
	spdk_trace_parser_next_entry(struct spdk_trace_parser *parser,
				     struct spdk_trace_parser_entry *entry)
	{
		return next_entry(parser, entry);
	}

} /* extern "C" */
