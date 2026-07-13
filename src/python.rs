use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::*;

fn to_pyerr(s: String) -> PyErr {
    PyValueError::new_err(s)
}

fn parse_bet_sizes(
    sizes: &[(String, String)],
) -> PyResult<[BetSizeOptions; 2]> {
    if sizes.len() != 2 {
        return Err(PyValueError::new_err("bet_sizes must have exactly 2 entries [OOP, IP]"));
    }
    Ok([
        BetSizeOptions::try_from((sizes[0].0.as_str(), sizes[0].1.as_str())).map_err(to_pyerr)?,
        BetSizeOptions::try_from((sizes[1].0.as_str(), sizes[1].1.as_str())).map_err(to_pyerr)?,
    ])
}

fn parse_donk_sizes(s: Option<&str>) -> PyResult<Option<DonkSizeOptions>> {
    match s {
        Some(s) => Ok(Some(DonkSizeOptions::try_from(s).map_err(to_pyerr)?)),
        None => Ok(None),
    }
}

fn parse_board_state(s: &str) -> PyResult<BoardState> {
    match s {
        "flop" | "Flop" => Ok(BoardState::Flop),
        "turn" | "Turn" => Ok(BoardState::Turn),
        "river" | "River" => Ok(BoardState::River),
        _ => Err(PyValueError::new_err(format!("Invalid board state: {s}"))),
    }
}

#[pyclass(name = "PostFlopGame")]
struct PyPostFlopGame {
    inner: PostFlopGame,
}

#[pymethods]
impl PyPostFlopGame {
    #[new]
    #[pyo3(signature = (
        oop_range,
        ip_range,
        flop,
        starting_pot,
        effective_stack,
        flop_bet_sizes,
        turn_bet_sizes,
        river_bet_sizes,
        turn=None,
        river=None,
        initial_state="flop",
        rake_rate=0.0,
        rake_cap=0.0,
        turn_donk_sizes=None,
        river_donk_sizes=None,
        add_allin_threshold=1.5,
        force_allin_threshold=0.15,
        merging_threshold=0.1,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        oop_range: &str,
        ip_range: &str,
        flop: &str,
        starting_pot: i32,
        effective_stack: i32,
        flop_bet_sizes: Vec<(String, String)>,
        turn_bet_sizes: Vec<(String, String)>,
        river_bet_sizes: Vec<(String, String)>,
        turn: Option<&str>,
        river: Option<&str>,
        initial_state: &str,
        rake_rate: f64,
        rake_cap: f64,
        turn_donk_sizes: Option<&str>,
        river_donk_sizes: Option<&str>,
        add_allin_threshold: f64,
        force_allin_threshold: f64,
        merging_threshold: f64,
    ) -> PyResult<Self> {
        let card_config = CardConfig {
            range: [
                oop_range.parse().map_err(to_pyerr)?,
                ip_range.parse().map_err(to_pyerr)?,
            ],
            flop: flop_from_str(flop).map_err(to_pyerr)?,
            turn: match turn {
                Some(s) => card_from_str(s).map_err(to_pyerr)?,
                None => NOT_DEALT,
            },
            river: match river {
                Some(s) => card_from_str(s).map_err(to_pyerr)?,
                None => NOT_DEALT,
            },
        };

        let tree_config = TreeConfig {
            initial_state: parse_board_state(initial_state)?,
            starting_pot,
            effective_stack,
            rake_rate,
            rake_cap,
            flop_bet_sizes: parse_bet_sizes(&flop_bet_sizes)?,
            turn_bet_sizes: parse_bet_sizes(&turn_bet_sizes)?,
            river_bet_sizes: parse_bet_sizes(&river_bet_sizes)?,
            turn_donk_sizes: parse_donk_sizes(turn_donk_sizes)?,
            river_donk_sizes: parse_donk_sizes(river_donk_sizes)?,
            add_allin_threshold,
            force_allin_threshold,
            merging_threshold,
        };

        let action_tree = ActionTree::new(tree_config).map_err(to_pyerr)?;
        let inner = PostFlopGame::with_config(card_config, action_tree).map_err(to_pyerr)?;

        Ok(PyPostFlopGame { inner })
    }

    fn allocate_memory(&mut self, compress: bool) {
        self.inner.allocate_memory(compress);
    }

    fn solve(&mut self, max_iterations: u32, target_exploitability: f32, verbose: bool) -> f32 {
        solve(&mut self.inner, max_iterations, target_exploitability, verbose)
    }

    fn solve_step(&self, iteration: u32) {
        solve_step(&self.inner, iteration);
    }

    fn finalize(&mut self) {
        finalize(&mut self.inner);
    }

    fn exploitability(&self) -> f32 {
        compute_exploitability(&self.inner)
    }

    fn play(&mut self, action: usize) {
        self.inner.play(action);
    }

    fn back_to_root(&mut self) {
        self.inner.back_to_root();
    }

    fn cache_normalized_weights(&mut self) {
        self.inner.cache_normalized_weights();
    }

    fn strategy(&self) -> Vec<f32> {
        self.inner.strategy().to_vec()
    }

    fn equity(&self, player: usize) -> Vec<f32> {
        self.inner.equity(player)
    }

    fn expected_values(&self, player: usize) -> Vec<f32> {
        self.inner.expected_values(player)
    }

    fn available_actions(&self) -> Vec<String> {
        self.inner
            .available_actions()
            .iter()
            .map(|a| format!("{a:?}"))
            .collect()
    }

    fn private_cards(&self, player: usize) -> PyResult<Vec<String>> {
        holes_to_strings(self.inner.private_cards(player)).map_err(to_pyerr)
    }

    fn num_private_hands(&self, player: usize) -> usize {
        self.inner.private_cards(player).len()
    }

    fn normalized_weights(&self, player: usize) -> Vec<f32> {
        self.inner.normalized_weights(player).to_vec()
    }

    fn lock_current_strategy(&mut self, strategy: Vec<f32>) {
        self.inner.lock_current_strategy(&strategy);
    }

    fn unlock_current_strategy(&mut self) {
        self.inner.unlock_current_strategy();
    }

    fn current_player(&self) -> usize {
        self.inner.current_player()
    }

    fn is_chance_node(&self) -> bool {
        self.inner.is_chance_node()
    }

    fn is_terminal_node(&self) -> bool {
        self.inner.is_terminal_node()
    }

    fn possible_cards(&self) -> u64 {
        self.inner.possible_cards()
    }

    fn current_board(&self) -> PyResult<Vec<String>> {
        self.inner
            .current_board()
            .iter()
            .map(|&c| card_to_string(c))
            .collect::<Result<_, _>>()
            .map_err(to_pyerr)
    }

    fn history(&self) -> Vec<usize> {
        self.inner.history().to_vec()
    }

    fn memory_usage(&self) -> (u64, u64) {
        self.inner.memory_usage()
    }

    fn starting_pot(&self) -> i32 {
        self.inner.tree_config().starting_pot
    }

    #[pyo3(signature = (path, memo, compression_level=None))]
    fn save(&self, path: &str, memo: &str, compression_level: Option<i32>) -> PyResult<()> {
        save_data_to_file(&self.inner, memo, path, compression_level).map_err(to_pyerr)
    }

    #[staticmethod]
    #[pyo3(signature = (path, max_memory=None))]
    fn load(path: &str, max_memory: Option<u64>) -> PyResult<Self> {
        let (game, _memo) =
            load_data_from_file::<PostFlopGame, &str>(path, max_memory).map_err(to_pyerr)?;
        Ok(PyPostFlopGame { inner: game })
    }
}

#[pyfunction]
fn compute_average_py(values: Vec<f32>, weights: Vec<f32>) -> f32 {
    crate::compute_average(&values, &weights)
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyPostFlopGame>()?;
    m.add_function(wrap_pyfunction!(compute_average_py, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
