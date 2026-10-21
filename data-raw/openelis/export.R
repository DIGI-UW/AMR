# Translate a deliberately bounded source scope; never guess a clinical context.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 5L) stop("Expected: library, organisms, guidelines, version, output")
.libPaths(c(args[1], .libPaths()))
suppressPackageStartupMessages(library(AMR))
Sys.setlocale("LC_COLLATE", "C")
options(scipen = 999, digits = 15)
organism_codes <- sort(unique(readLines(args[2])))
guidelines <- sort(unique(readLines(args[3])))
version <- args[4]
out <- args[5]
mo <- as.data.frame(microorganisms, stringsAsFactors = FALSE)
ab <- as.data.frame(antimicrobials, stringsAsFactors = FALSE)
bp <- as.data.frame(clinical_breakpoints, stringsAsFactors = FALSE)
bp$source_row <- seq_len(nrow(bp))
bp <- bp[bp$type == "human" & bp$guideline %in% guidelines, , drop = FALSE]
if (!all(guidelines %in% bp$guideline)) stop("Unknown or empty guideline")
if (!all(organism_codes %in% as.character(mo$mo))) stop("Unknown organism code")
empty <- function(x) is.na(x) | x == ""
write_csv <- function(x, name) {
  write.table(
    x, file.path(out, name), sep = ",", row.names = FALSE,
    col.names = TRUE, na = "", qmethod = "double", fileEncoding = "UTF-8"
  )
}
rows <- list()
vectors <- list()
coverage <- list()
used_source_rows <- integer()
used_agents <- character()
add_coverage <- function(code, guideline, agent, method, status, reason, ids) {
  coverage[[length(coverage) + 1L]] <<- data.frame(
    organism_code = code, guideline = guideline, antibiotic_code = agent,
    method = method, status = status, reason = reason,
    source_rows = paste(sort(ids), collapse = ";"), stringsAsFactors = FALSE
  )
}
valid_mic <- suppressWarnings(as.numeric(as.character(AMR:::VALID_MIC_LEVELS)))
valid_mic <- sort(unique(valid_mic[is.finite(valid_mic) & valid_mic > 0]))
for (code in organism_codes) {
  organism <- mo[as.character(mo$mo) == code, , drop = FALSE]
  ancestor_names <- unlist(organism[c("genus", "family", "order", "class")])
  ancestors <- as.character(mo$mo[match(ancestor_names, mo$fullname)])
  groups <- as.character(microorganisms.groups$mo_group[
    as.character(microorganisms.groups$mo) == code
  ])
  gram <- mo_gramstain(code, language = "en")
  broad <- if (gram == "Gram-negative") "B_GRAMN" else if (gram == "Gram-positive") "B_GRAMP" else character()
  if (identical(organism$oxygen_tolerance, "anaerobe")) {
    broad <- c(broad, "B_ANAER", if (gram == "Gram-negative") "B_ANAER-NEG" else "B_ANAER-POS")
  }
  relevant <- bp[as.character(bp$mo) %in% c(code, ancestors, groups, broad, "UNKNOWN"), , drop = FALSE]
  used_source_rows <- union(used_source_rows, relevant$source_row)
  for (guideline in guidelines) {
    current <- relevant[relevant$guideline == guideline, , drop = FALSE]
    for (agent in sort(unique(as.character(current$ab)))) {
      used_agents <- union(used_agents, agent)
      for (method in sort(unique(current$method[as.character(current$ab) == agent]))) {
        candidates <- current[as.character(current$ab) == agent & current$method == method, , drop = FALSE]
        reason <- ""
        if (method != "MIC") {
          reason <- "disk_content_context_not_supported"
        } else if (any(candidates$uti %in% TRUE | !empty(candidates$site))) {
          reason <- "site_route_or_uti_context_not_supported"
        } else {
          best <- candidates[candidates$rank_index == min(candidates$rank_index), , drop = FALSE]
          signatures <- unique(best[c("breakpoint_S", "breakpoint_R", "is_SDD", "note")])
          if (nrow(signatures) != 1L) reason <- "ambiguous_same_rank_rules"
          if (any(best$is_SDD %in% TRUE)) reason <- "sdd_not_supported"
          if (any(is.na(best$breakpoint_S) | is.na(best$breakpoint_R))) reason <- "no_or_incomplete_breakpoint"
          if (any(best$breakpoint_S <= 0.001, na.rm = TRUE)) reason <- "no_susceptible_category"
          if (any(!empty(best$note))) reason <- "conditional_note_requires_review"
        }
        if (reason != "") {
          add_coverage(code, guideline, agent, method, "UNSUPPORTED", reason, candidates$source_row)
          next
        }
        rule <- best[order(best$source_row), , drop = FALSE][1L, , drop = FALSE]
        s <- rule$breakpoint_S
        r <- rule$breakpoint_R
        if (!is.finite(s) || !is.finite(r) || s > r) stop("Invalid source thresholds")
        # Exercise accepted MIC values at both boundaries and adjacent off-dilution values.
        points <- unique(c(s, r, 2^(-10:10), 1.5, 3, 6, 12))
        for (boundary in c(s, r)) {
          points <- c(points, tail(valid_mic[valid_mic < boundary], 1L), head(valid_mic[valid_mic > boundary], 1L))
        }
        points <- sort(unique(points[is.finite(points) & points > 0]))
        mic <- suppressWarnings(as.mic(points))
        keep <- !is.na(mic) & as.numeric(mic) == points
        points <- points[keep]
        mic <- mic[keep]
        expected <- as.character(
          suppressMessages(
            suppressWarnings(
              as.sir(
                mic, mo = code, ab = agent, guideline = guideline, uti = FALSE,
                include_screening = FALSE, include_PKPD = FALSE,
                add_intrinsic_resistance = FALSE, info = FALSE
              )
            )
          )
        )
        translated <- ifelse(points <= s, "S", ifelse(points > r, "R", "I"))
        if (any(is.na(expected)) || !identical(expected, translated) ||
            !all(c(s, r) %in% points)) {
          add_coverage(code, guideline, agent, method, "UNSUPPORTED", "source_interpretation_mismatch", candidates$source_row)
          next
        }
        edition <- paste(sub("^EUCAST ", "", guideline), version, sep = "-")
        rows[[length(rows) + 1L]] <- data.frame(
          publisher = "EUCAST", version = edition,
          organism_or_group = organism$fullname, antibiotic_whonet_code = agent,
          method = "MIC", specimen_type_id = "", breakpoint_type = "MIC",
          susceptible_value = s, intermediate_lower_value = "", intermediate_upper_value = "",
          resistant_value = r, units = "mg/L", resistant_inclusive = "false",
          organism_source = "AMR", organism_code = code,
          antibiotic_source = "AMR", antibiotic_code = agent,
          notes = paste("AMR source row", rule$source_row, "target", as.character(rule$mo)),
          stringsAsFactors = FALSE
        )
        vectors[[length(vectors) + 1L]] <- data.frame(
          publisher = "EUCAST", version = edition, organism_code = code,
          antibiotic_code = agent, method = "MIC", value = points,
          expected = expected, stringsAsFactors = FALSE
        )
        add_coverage(code, guideline, agent, method, "SUPPORTED", "", candidates$source_row)
      }
    }
  }
}
if (!length(rows)) stop("Scope produces no supported breakpoints")
rules <- do.call(rbind, rows)
if (!all(paste(sub("^EUCAST ", "", guidelines), version, sep = "-") %in% rules$version)) stop("An edition has no supported rules")
organisms <- mo[match(organism_codes, as.character(mo$mo)), , drop = FALSE]
whonet <- vapply(seq_len(nrow(organisms)), function(i) {
  codes <- microorganisms.codes
  matches <- as.character(codes$code[which(
    as.character(codes$mo) == organism_codes[i] &
      codes$source == paste0("WHONET (", organisms$fullname[i], ")")
  )])
  if (length(unique(matches)) == 1L) tolower(matches[1]) else ""
}, character(1))
write_csv(
  data.frame(
    source = "AMR", code = organism_codes, displayName = organisms$fullname,
    whonetCode = whonet,
    gramStain = toupper(gsub("-", "_", mo_gramstain(organism_codes, language = "en"))),
    stringsAsFactors = FALSE
  ),
  "organisms.csv"
)
agents <- ab[match(sort(used_agents), as.character(ab$ab)), , drop = FALSE]
if (any(is.na(agents$ab))) stop("Unresolved antimicrobial")
write_csv(
  data.frame(
    source = "AMR", code = as.character(agents$ab), displayName = agents$name,
    whonetCode = as.character(agents$ab), antibioticClass = agents$atc_group2,
    stringsAsFactors = FALSE
  ),
  "antimicrobials.csv"
)
write_csv(rules, "breakpoints.csv")
write_csv(do.call(rbind, vectors), "interpretation-vectors.csv")
write_csv(do.call(rbind, coverage), "coverage.csv")
# Every selected source row remains accounted for, including blockers and out-of-scope taxa.
write_csv(
  data.frame(
    source_row = bp$source_row, guideline = bp$guideline,
    organism_code = as.character(bp$mo), antibiotic_code = as.character(bp$ab),
    method = bp$method,
    status = ifelse(bp$source_row %in% used_source_rows, "SEE_COVERAGE", "OUTSIDE_ORGANISM_SCOPE"),
    stringsAsFactors = FALSE
  ),
  "source-rows.csv"
)
